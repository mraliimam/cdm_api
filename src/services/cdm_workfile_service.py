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
import tempfile
import datetime
import subprocess

import boto3
from pydub import AudioSegment
from src.extensions import db
from src.models.cc_audio import CCAudio
from src.models.workfile import AAIAPWORKFILE

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
