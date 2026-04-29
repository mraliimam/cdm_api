"""
CDM L4 Workfile Provisioning Service

Handles the full audio-file pipeline that must run before an L4 workfile can
be handed to an IAP evaluator:

  1. Download the raw audio from HMS R2 (source bucket).
  2. Validate the downloaded file is playable audio.
  3. Compress to MP3 and convert to WAV.
  4. Upload the converted originals back to HMS R2.
  5. Split the compressed file by the time range stored in CC_AUDIO
     (START_AUDIO_TIME / END_AUDIO_TIME).  If both are 0 the full file is
     treated as a single chunk.
  6. Upload each chunk + empty JSON stubs to the IAP R2 bucket under the
     evaluator's ToDo folder.
  7. Insert one AA_IAP_WORKFILE row per chunk, already assigned to the
     evaluator, so the file appears in the evaluator's IAP queue immediately.
  8. Return the list of newly created AAIAPWORKFILE ORM objects so the caller
     can build the API response from real workfile data.

Environment variables
---------------------
HMS_R2_ENDPOINT_URL       Cloudflare R2 endpoint for the HMS bucket
HMS_R2_ACCESS_KEY_ID
HMS_R2_SECRET_ACCESS_KEY
HMS_R2_BUCKET             Name of the HMS source bucket

R2_ENDPOINT_URL           Cloudflare R2 endpoint for the IAP bucket
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET                 Name of the IAP destination bucket
"""

import json
import logging
import os
import re
import tempfile
import datetime
import subprocess

import boto3
import botocore
from pydub import AudioSegment
from extensions import db
from models.aa_record_model import AARecord
from models.cc_audio import CCAudio
from models.workfile import AAIAPWORKFILE

logger = logging.getLogger(__name__)

# Point pydub at the system ffmpeg/ffprobe installed in the Docker image.
# This prevents the "Couldn't find ffprobe" RuntimeWarning and ensures
# AudioSegment.from_file() works on Lambda.
AudioSegment.converter = "/usr/local/bin/ffmpeg"
AudioSegment.ffmpeg    = "/usr/local/bin/ffmpeg"
AudioSegment.ffprobe   = "/usr/local/bin/ffprobe"

HMS_R2_BUCKET = os.environ.get('HMS_R2_BUCKET', 'rawaudio')
R2_BUCKET     = os.environ.get('R2_BUCKET',     'dev-iap-data-operational')


# ---------------------------------------------------------------------------
# S3 clients
# ---------------------------------------------------------------------------

def _hms_s3():
    """Return a boto3 client configured for the HMS R2 bucket."""
    return boto3.client(
        's3',
        endpoint_url         = os.environ.get('R2_ENDPOINT'),
        aws_access_key_id    = os.environ.get('R2_ACCESS_KEY_ID'),
        aws_secret_access_key= os.environ.get('R2_SECRET_ACCESS_KEY'),
    )


def _iap_s3():
    """Return a boto3 client configured for the IAP R2 bucket."""
    return boto3.client(
        's3',
        endpoint_url         = os.environ.get('R2_ENDPOINT'),
        aws_access_key_id    = os.environ.get('R2_ACCESS_KEY_ID'),
        aws_secret_access_key= os.environ.get('R2_SECRET_ACCESS_KEY'),
    )


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------

# def _resolve_ffprobe() -> str:
#     """
#     Return a path to an executable ffprobe binary.

#     On read-only environments (e.g. AWS Lambda at /var/task) the bundled binary
#     cannot be executed in-place.  Copy it to /tmp on first call so it can be
#     chmod'd and run.
#     """
#     project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#     # Prefer a Linux binary if present alongside the .exe
#     for name in ("ffprobe", "ffprobe.exe"):
#         bundled = os.path.join(project_root, name)
#         if not os.path.isfile(bundled):
#             continue

#         # If it is already executable, use it directly
#         if os.access(bundled, os.X_OK):
#             return bundled

#         # Otherwise copy to /tmp (writable on Lambda) and chmod
#         tmp_path = os.path.join("/tmp", name)
#         if not os.path.isfile(tmp_path):
#             import shutil
#             shutil.copy2(bundled, tmp_path)
#         os.chmod(tmp_path, 0o755)
#         return tmp_path

#     return "ffprobe"  # fall back to PATH


# _FFPROBE = _resolve_ffprobe()


# def is_valid_audio(file_path):
#     try:
#         result = subprocess.run(
#             [_FFPROBE, "-v", "error", "-show_entries",
#              "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
#             capture_output=True, text=True
#         )
#         return result.returncode == 0 and "audio" in result.stdout
#     except Exception as e:
#         logging.error(f"Audio validation error: {e}")
#         return False
def is_valid_audio(file_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True
        )
        return result.returncode == 0 and "audio" in result.stdout
    except Exception as e:
        logging.error(f"Audio validation error: {e}")
        return False

def split_audio_by_ranges(
    input_mp3: str,
    output_dir: str,
    base_name: str,
    ranges: list,
) -> list:
    """
    Split input_mp3 into chunks defined by ranges (list of (start_sec, end_sec)).

    If ranges == [(0, 0)] the full audio is returned as a single chunk.
    Returns a list of absolute file paths to the produced MP3 chunk files.
    """
    audio       = AudioSegment.from_file(input_mp3)
    chunk_files = []

    if ranges == [(0, 0)]:
        chunk_path = os.path.join(output_dir, f"{base_name}_part1.mp3")
        audio.export(chunk_path, format="mp3")
        logger.debug("split_audio_by_ranges: single-chunk (no split) → %s", chunk_path)
        return [chunk_path]

    for i, (start_time, end_time) in enumerate(ranges, start=1):
        start_ms = int(start_time) * 1000
        end_ms   = int(end_time)   * 1000

        if start_ms < 0 or end_ms > len(audio):
            raise ValueError(
                f"Range {i} ({start_time}–{end_time}s) is out of bounds "
                f"for audio of length {len(audio) / 1000:.1f}s"
            )
        if start_ms >= end_ms:
            raise ValueError(
                f"Invalid range {i}: start ({start_time}s) >= end ({end_time}s)"
            )

        chunk      = audio[start_ms:end_ms]
        chunk_path = os.path.join(output_dir, f"{base_name}_part{i}.mp3")
        chunk.export(chunk_path, format="mp3")
        chunk_files.append(chunk_path)
        logger.debug("split_audio_by_ranges: chunk %d → %s", i, chunk_path)

    return chunk_files


# ---------------------------------------------------------------------------
# Public provisioning entry point
# ---------------------------------------------------------------------------

def provision_l4_workfile(
    cc_audio: CCAudio,
    evaluator_id: int,
    username: str,
) -> list:
    """
    Run the full L4 audio-provisioning pipeline for one CC_AUDIO record and
    one IAP evaluator.

    Parameters
    ----------
    cc_audio     : CCAudio ORM object — the selected audio record.
    evaluator_id : int                 — AA_IAP_USERS.ID of the evaluator.
    username     : str                 — AA_IAP_USERS.USERNAME (used for S3 paths).

    Returns
    -------
    list of AAIAPWORKFILE — the newly flushed (but not yet committed) ORM rows,
    one per audio chunk.  The caller is responsible for calling db.session.commit().

    Raises
    ------
    ValueError  if the downloaded file is not valid audio.
    Exception   re-raised from boto3 on S3 errors.
    """
    hms_s3 = _hms_s3()
    iap_s3 = _iap_s3()
    # AudioSegment.ffmpeg = "ffmpeg"
    # AudioSegment.ffprobe = "ffprobe"
    # ------------------------------------------------------------------ #
    # 1. Download raw audio from HMS R2                                   #
    # ------------------------------------------------------------------ #
    r2_key  = f"data/Dev/RawAudioFiles/{cc_audio.FILEPATH}"
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, os.path.basename(cc_audio.FILEPATH))

    logger.info("provision_l4_workfile: downloading %s from HMS R2", r2_key)
    hms_s3.download_file(HMS_R2_BUCKET, r2_key, filepath)

    # ------------------------------------------------------------------ #
    # 2. Validate                                                          #
    # ------------------------------------------------------------------ #
    if not is_valid_audio(filepath):
        raise ValueError(
            f"Downloaded file is not valid audio: {cc_audio.FILEPATH}"
        )

    # ------------------------------------------------------------------ #
    # 3. Rename, compress, convert                                        #
    # ------------------------------------------------------------------ #
    renamed_filename = f"L4_{cc_audio.ID}Production"

    compressed_dir = os.path.join(tmp_dir, "compressed")
    wav_dir        = os.path.join(tmp_dir, "wav")
    chunks_dir     = os.path.join(tmp_dir, "chunks")
    for d in (compressed_dir, wav_dir, chunks_dir):
        os.makedirs(d, exist_ok=True)

    compressed_filepath = os.path.join(compressed_dir, f"{renamed_filename}.mp3")
    wav_filepath        = os.path.join(wav_dir,        f"{renamed_filename}.wav")

    print(f'compressed_filepath {compressed_filepath}')
    audio = AudioSegment.from_file(filepath)
    audio.export(compressed_filepath, format="mp3")
    audio.export(wav_filepath,        format="wav")

    # ------------------------------------------------------------------ #
    # 4. Upload converted originals to HMS R2                             #
    # ------------------------------------------------------------------ #
    hms_s3.upload_file(
        compressed_filepath, HMS_R2_BUCKET,
        f"data/Dev/RawAudioFiles/ConvertedAudio/compressed/{renamed_filename}.mp3",
    )
    hms_s3.upload_file(
        wav_filepath, HMS_R2_BUCKET,
        f"data/Dev/RawAudioFiles/ConvertedAudio/wav/{renamed_filename}_supervisor.wav",
    )
    logger.info("provision_l4_workfile: uploaded converted originals to HMS R2")
    print("provision_l4_workfile: uploaded converted originals to HMS R2")

    # ------------------------------------------------------------------ #
    # 5. Split by time range                                              #
    # ------------------------------------------------------------------ #
    start_t = getattr(cc_audio, 'START_AUDIO_TIME', None) or 0
    end_t   = getattr(cc_audio, 'END_AUDIO_TIME',   None) or 0
    ranges  = [(start_t, end_t)]

    chunk_files = split_audio_by_ranges(
        compressed_filepath, chunks_dir, renamed_filename, ranges
    )

    # ------------------------------------------------------------------ #
    # 6-7. Upload chunks + JSON stubs → insert AA_IAP_WORKFILE rows       #
    # ------------------------------------------------------------------ #
    now_str          = datetime.datetime.utcnow().strftime('%Y/%m/%d %H:%M:%S')
    created_workfiles = []

    for chunk_index, chunk_file in enumerate(chunk_files, start=1):
        chunk_name = f"{renamed_filename}_part{chunk_index}"

        audio_key    = f"data/Dev/ToDo/{username}/Audio/{chunk_name}.WKFL"
        model_key    = f"data/Dev/ToDo/{username}/ModelPred/{chunk_name}.json"
        filesave_key = f"data/Dev/ToDo/{username}/FileSave/{chunk_name}.json"

        # Upload audio chunk to IAP bucket
        iap_s3.upload_file(chunk_file, R2_BUCKET, audio_key)
        logger.info("provision_l4_workfile: uploaded chunk → %s", audio_key)
        print("provision_l4_workfile: uploaded chunk →")

        # Upload empty JSON stubs
        for tmp_path, dest_key in [
            (os.path.join(tmp_dir, f"{chunk_name}_model.json"),    model_key),
            (os.path.join(tmp_dir, f"{chunk_name}_filesave.json"), filesave_key),
        ]:
            with open(tmp_path, "w") as fh:
                json.dump({"data": []}, fh)
            iap_s3.upload_file(tmp_path, R2_BUCKET, dest_key)

        # ---------------------------------------------------------------- #
        # Insert AA_IAP_WORKFILE row using the ORM                         #
        # (already assigned to evaluator — no separate _assign_workfile)   #
        # ---------------------------------------------------------------- #
        wf = AAIAPWORKFILE(
            WORKFILE_NAME      = f"{chunk_name}.WKFL",
            WORKFILE_STATUS    = 'ToDo',
            ASSIGNED_USER_ID   = evaluator_id,
            AUDIO_FILEPATH     = audio_key,
            AUDIO_STATUS       = 'ToDo',
            FILESAVE_FILEPATH  = filesave_key,
            FILESAVE_STATUS    = 'ToDo',
            MODELPRED_FILEPATH = model_key,
            MODELPRED_STATUS   = 'ToDo',
            LAST_MOVED_DT      = now_str,
            LAST_MOVED_BY      = 'CDM',
            CURR_ROW_FL        = 'Y',
            STAGE              = 'L4',
            REVIEW_FL          = 'N',
            CC_AUDIO_ID        = str(cc_audio.ID),
            TEST_STATIC_ID     = str(chunk_index),
            TO_DO_MOVED_DTS    = now_str,
        )
        db.session.add(wf)
        db.session.flush()   # populate wf.ID before returning
        created_workfiles.append(wf)
        logger.info(
            "provision_l4_workfile: created AAIAPWORKFILE ID=%s for chunk %s",
            wf.ID, chunk_name,
            print(f'provision_l4_workfile: created AAIAPWORKFILE ID={ wf.ID} for chunk {chunk_name}')
        )
    print(f'created workfile {created_workfiles}')
    return created_workfiles


# ---------------------------------------------------------------------------
# L3 provisioning (AA_RECORD-sourced)
#
# Mirrors `L3_job.py` end-to-end:
#   1. process_splitting_auto_segment()  →  _split_aa_record_segment()
#      Slice the parent's converted WAV to the AA_RECORD's time range and
#      stamp the result at HMS R2: data/Dev/AA_RECORD/L3_<rec.ID>.wav
#      (idempotent — skipped if already there).
#   2. main loop                          →  provision_l3_workfile_from_aa_record()
#      Upload the slice + empty ModelPred / FileSave stubs to the
#      evaluator's IAP ToDo folder, then insert an AA_IAP_WORKFILE row
#      already assigned to that evaluator (STAGE='L3').
# ---------------------------------------------------------------------------

def _safe_seg_seconds(rec: AARecord) -> float:
    """Pull a usable segment length out of AA_RECORD (defaults to 60s)."""
    try:
        if rec.SEGMENT_START_TIME is not None and rec.SEGMENT_END_TIME is not None:
            secs = float(rec.SEGMENT_END_TIME) - float(rec.SEGMENT_START_TIME)
            if secs > 0:
                return secs
    except (TypeError, ValueError):
        pass
    try:
        if rec.RECORD_LENGTH is not None:
            return float(rec.RECORD_LENGTH)
    except (TypeError, ValueError):
        pass
    return 60.0


def _resolve_parent_workfile_basename(parent_workfile_name: str) -> str:
    """
    Strip a parent AA_IAP_WORKFILE.WORKFILE_NAME down to the base audio key
    used inside HMS R2's ConvertedAudio/wav folder. Mirrors L3_job.py:

        re.sub(r'_part\\d+', '', WORKFILE_NAME.split('.')[0])

    Examples:
        'L4_42Production_part1.WKFL' -> 'L4_42Production'
        'L4_42Production.WKFL'       -> 'L4_42Production'
    """
    if not parent_workfile_name:
        return None
    no_ext = parent_workfile_name.split('.')[0]
    return re.sub(r'_part\d+', '', no_ext) or None


def _upload_if_not_exists(s3_client, bucket: str, local_path: str, s3_key: str) -> bool:
    """
    Upload local_path → s3://bucket/s3_key only when the destination object
    does not already exist. Returns True if a new upload was performed,
    False if the object already existed. Re-raises any other client errors.

    Mirrors `upload_if_not_exists` in L3_job.py so re-running the L3
    provisioning step for an already-sliced AA_RECORD is a no-op.
    """
    try:
        s3_client.head_object(Bucket=bucket, Key=s3_key)
        return False
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get('Error', {}).get('Code')
        if code in ('404', 'NoSuchKey', 'NotFound'):
            s3_client.upload_file(local_path, bucket, s3_key)
            return True
        raise


def _split_aa_record_segment(
    aa_record: AARecord,
    parent_workfile: AAIAPWORKFILE,
    work_dir: str,
) -> str:
    """
    Mirror of `process_splitting_auto_segment` in L3_job.py.

      • Source: data/Dev/RawAudioFiles/ConvertedAudio/wav/{parent_basename}.wav
        on HMS R2, where parent_basename is derived from the parent
        AA_IAP_WORKFILE.WORKFILE_NAME (with `_partN` and the `.WKFL`
        extension removed).
      • Slice to AA_RECORD.SEGMENT_START_TIME / SEGMENT_END_TIME.
      • Stamp at HMS R2 data/Dev/AA_RECORD/L3_<rec.ID>.wav (skipped if
        already present).

    Returns the local path of the sliced WAV — the caller re-uses it
    when uploading to the evaluator's IAP ToDo folder so we don't have
    to round-trip through HMS R2 a second time.
    """
    audio_key_as_wf_id = _resolve_parent_workfile_basename(parent_workfile.WORKFILE_NAME)
    if not audio_key_as_wf_id:
        raise ValueError(
            f"AA_RECORD {aa_record.ID}: cannot resolve parent workfile basename "
            f"(parent WORKFILE_NAME={parent_workfile.WORKFILE_NAME!r})"
        )

    hms_s3 = _hms_s3()

    source_key = f"data/Dev/RawAudioFiles/ConvertedAudio/wav/{audio_key_as_wf_id}.wav"
    dest_key   = f"data/Dev/AA_RECORD/L3_{aa_record.ID}.wav"
    src_path   = os.path.join(work_dir, f"L3_src_{aa_record.ID}.wav")
    dest_path  = os.path.join(work_dir, f"L3_{aa_record.ID}.wav")

    logger.info("provision_l3_workfile: downloading parent %s from HMS R2", source_key)
    hms_s3.download_file(HMS_R2_BUCKET, source_key, src_path)

    if not is_valid_audio(src_path):
        raise ValueError(
            f"AA_RECORD {aa_record.ID}: parent audio not playable ({source_key})"
        )

    seg_start = float(aa_record.SEGMENT_START_TIME) if aa_record.SEGMENT_START_TIME else 0.0
    seg_end   = (
        float(aa_record.SEGMENT_END_TIME)
        if aa_record.SEGMENT_END_TIME
        else seg_start + _safe_seg_seconds(aa_record)
    )

    audio    = AudioSegment.from_file(src_path)
    start_ms = max(0, int(seg_start * 1000))
    end_ms   = min(len(audio), int(seg_end * 1000)) or len(audio)
    if end_ms <= start_ms:
        raise ValueError(
            f"AA_RECORD {aa_record.ID}: invalid segment range "
            f"start={seg_start}s end={seg_end}s"
        )

    audio[start_ms:end_ms].export(dest_path, format="wav")

    if not is_valid_audio(dest_path):
        raise ValueError(
            f"AA_RECORD {aa_record.ID}: sliced audio failed validation"
        )

    uploaded = _upload_if_not_exists(hms_s3, HMS_R2_BUCKET, dest_path, dest_key)
    logger.info(
        "provision_l3_workfile: AA_RECORD %s slice %s HMS R2 %s",
        aa_record.ID,
        "uploaded to" if uploaded else "already present at",
        dest_key,
    )
    return dest_path


def provision_l3_workfile_from_aa_record(
    aa_record: AARecord,
    evaluator_id: int,
    username: str,
    parent_audio: CCAudio = None,
) -> AAIAPWORKFILE:
    """
    Provision a brand-new L3 IAP workfile for one AA_RECORD row.

    Replicates the full L3_job.py pipeline:

      1. Resolve the parent AA_IAP_WORKFILE via AA_RECORD.WORKFILE_ID and
         derive the audio basename (`audio_key_as_workfile_id` in
         L3_job.py) by stripping `_partN` and the `.WKFL` extension off
         WORKFILE_NAME.
      2. Download `data/Dev/RawAudioFiles/ConvertedAudio/wav/<basename>.wav`
         from HMS R2, slice it to AA_RECORD.SEGMENT_START_TIME /
         SEGMENT_END_TIME, validate, and stamp at HMS R2
         `data/Dev/AA_RECORD/L3_<rec.ID>.wav` (idempotent — only uploaded
         if missing).
      3. Upload the same sliced WAV plus empty ModelPred / FileSave JSON
         stubs to the IAP R2 bucket under the evaluator's ToDo folder
         using the workfile name `L3_<rec.ID>.WKFL`.
      4. Insert one AA_IAP_WORKFILE row with STAGE='L3', AA_RECORD_ID
         populated and the evaluator already assigned (mirrors L4
         provisioning so no separate `_assign_workfile` call is needed).

    Returns the freshly-flushed (uncommitted) AAIAPWORKFILE — the caller
    is responsible for db.session.commit().

    Raises
    ------
    ValueError  if the parent workfile / parent audio cannot be resolved,
                or if downloaded / sliced audio fails validation.
    Exception   re-raised from boto3 on R2 errors.
    """
    # ------------------------------------------------------------------ #
    # 1. Resolve parent AA_IAP_WORKFILE (source of the converted WAV)    #
    # ------------------------------------------------------------------ #
    parent_wf = None
    if aa_record.WORKFILE_ID and str(aa_record.WORKFILE_ID).isdigit():
        parent_wf = AAIAPWORKFILE.query.get(int(aa_record.WORKFILE_ID))

    if parent_wf is None or not parent_wf.WORKFILE_NAME:
        raise ValueError(
            f"AA_RECORD {aa_record.ID}: cannot resolve parent AA_IAP_WORKFILE "
            f"(WORKFILE_ID={aa_record.WORKFILE_ID})"
        )

    # parent_audio is optional — used only to stamp CC_AUDIO_ID on the
    # new workfile row for traceability. Resolve from the parent workfile
    # when the caller didn't supply it.
    if parent_audio is None and parent_wf.CC_AUDIO_ID:
        try:
            parent_audio = CCAudio.query.get(int(parent_wf.CC_AUDIO_ID))
        except (TypeError, ValueError):
            parent_audio = None

    base_name = f"L3_{aa_record.ID}"   # L3_job.py naming — no `_partN` suffix
    tmp_dir   = tempfile.mkdtemp()
    iap_s3    = _iap_s3()

    # ------------------------------------------------------------------ #
    # 2. Slice + stamp at HMS R2 data/Dev/AA_RECORD/L3_<rec.ID>.wav      #
    # ------------------------------------------------------------------ #
    chunk_local = _split_aa_record_segment(aa_record, parent_wf, tmp_dir)

    # ------------------------------------------------------------------ #
    # 3. Upload chunk + JSON stubs to evaluator's ToDo folder            #
    # ------------------------------------------------------------------ #
    audio_key    = f"data/Dev/ToDo/{username}/Audio/{base_name}.WKFL"
    model_key    = f"data/Dev/ToDo/{username}/ModelPred/{base_name}.json"
    filesave_key = f"data/Dev/ToDo/{username}/FileSave/{base_name}.json"

    iap_s3.upload_file(chunk_local, R2_BUCKET, audio_key)
    logger.info("provision_l3_workfile: uploaded chunk → %s", audio_key)

    for tmp_path, dest_key in [
        (os.path.join(tmp_dir, f"{base_name}_model.json"),    model_key),
        (os.path.join(tmp_dir, f"{base_name}_filesave.json"), filesave_key),
    ]:
        with open(tmp_path, "w") as fh:
            json.dump({"data": []}, fh)
        iap_s3.upload_file(tmp_path, R2_BUCKET, dest_key)

    # ------------------------------------------------------------------ #
    # 4. Insert AA_IAP_WORKFILE row, already assigned to the evaluator   #
    # ------------------------------------------------------------------ #
    now_str = datetime.datetime.utcnow().strftime('%Y/%m/%d %H:%M:%S')
    wf = AAIAPWORKFILE(
        WORKFILE_NAME      = f"{base_name}.WKFL",
        WORKFILE_STATUS    = 'ToDo',
        ASSIGNED_USER_ID   = evaluator_id,
        AUDIO_FILEPATH     = audio_key,
        AUDIO_STATUS       = 'ToDo',
        FILESAVE_FILEPATH  = filesave_key,
        FILESAVE_STATUS    = 'ToDo',
        MODELPRED_FILEPATH = model_key,
        MODELPRED_STATUS   = 'ToDo',
        LAST_MOVED_DT      = now_str,
        LAST_MOVED_BY      = 'CDM',
        CURR_ROW_FL        = 'Y',
        STAGE              = 'L3',
        REVIEW_FL          = 'N',
        CC_AUDIO_ID        = str(parent_audio.ID) if parent_audio else None,
        AA_RECORD_ID       = str(aa_record.ID),
        TEST_STATIC_ID     = 1,
        TO_DO_MOVED_DTS    = now_str,
    )
    db.session.add(wf)
    db.session.flush()
    logger.info(
        "provision_l3_workfile: created AA_IAP_WORKFILE ID=%s for AA_RECORD %s",
        wf.ID, aa_record.ID,
    )
    return wf
