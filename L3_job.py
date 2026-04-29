
# df is aa_record rows
for index, row in df.iterrows(): 

    # Build the R2 object key (instead of local FOLDER_DIR path)
    process_splitting_auto_segment(session, row['ID'])
    object_key = f"data/Dev/AA_RECORD/L3_{row['ID']}.wav"

    # --- Check if file exists in R2 ---
    try:
        s3.head_object(Bucket=HMS_R2_BUCKET, Key=object_key)
    except s3.exceptions.ClientError:
        logging.info(f"File not found in R2: {object_key}")
        continue  # Skip this row

    # --- Download temporarily to validate ---
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        s3.download_file(HMS_R2_BUCKET, object_key, tmp_file.name)
        local_path = tmp_file.name

    # --- Validate audio file ---
    isValid = is_valid_audio(local_path)
    if not isValid:
        logging.info(f"Invalid audio file: {object_key}")
        os.remove(local_path)
        continue  # Skip this row

    # --- File is valid ---
    logging.info(row)
    filename, file_extension = os.path.splitext(os.path.basename(object_key))

    # cleanup local file

    
    #Determine who to assign this workfile to 
    # if row['AUDIO_TYPE'] == 'PRACTICE': 
    #     #assgin to all users requriing practice 
    #     df_username = pd.read_sql(""" select * FROM AA_IAP_USERS where USER_STAGE = 'PRACTICE' """, conn)

    # elif row['AUDIO_TYPE'] == 'TEST': 
    #     df_username = pd.read_sql(""" select * FROM AA_IAP_USERS where USER_STAGE = 'TEST' """, conn)

    # else:
                            
    

        
        

        #Get users table of users that will be assigned files
        df_username = pd.read_sql(""" select * FROM AA_IAP_USERS where ID = """ + str(assigned_users), conn)

        username = df_username['USERNAME'].iloc[0]
        logging.info(f"Assigned user: {username}")


    for index, userline in df_username.iterrows(): 
        
        username = userline['USERNAME']
        assigned_user = userline['ID']
        logging.info(f"Assigning files to user: {username}")

        workfile_name = filename
        workfile_status = 'ToDo'

        #Move all workfiles to assigned user's ToDo Folder

        prefix = f"data/Dev/ToDo/{username}/Audio/"
        logging.info(f"Checking R2 prefix: {prefix}")

        response = s3.list_objects_v2(Bucket=R2_BUCKET, Prefix=prefix)

        # num_files_assigned_to_user = 0
        # if "Contents" in response:
        #     num_files_assigned_to_user = len(response["Contents"])

        # logging.info(f"num_files_assigned_to_user: {num_files_assigned_to_user}")

        # if num_files_assigned_to_user > 30:
        #     print("count file *****")
        #     continue

        
        filepath =local_path
        
        s3_key = f"data/Dev/ToDo/{username}/Audio/{workfile_name}.WKFL"
        s3.upload_file(filepath, R2_BUCKET, s3_key)
        logging.info(f"Uploaded file to s3://{R2_BUCKET}/{s3_key}")
        os.remove(local_path)

        

        model_pred_path = f"/tmp/{workfile_name}_modelpred.json"

        # recognizer = OfflineRecognizer.OfflineRecognizer(vad_model_str="pyannote/voice-activity-detection", asr_model_path="models/Conformer-ctc/model.nemo")

        # transcriptions = recognizer.transcripe_file(wav_filename)

        transcriptions = {"data":[]}

        with open(model_pred_path, "w", encoding="utf-8") as outfile:
            json.dump(transcriptions, outfile)

        modelpred_s3_key = f"data/Dev/ToDo/{username}/ModelPred/{workfile_name}.json"
        s3.upload_file(model_pred_path, R2_BUCKET, modelpred_s3_key)
        logging.info(f"Uploaded model predictions to s3://{R2_BUCKET}/{modelpred_s3_key}")
        # model_filepath = 'C:/Users/Administrator/git_projects/DATA/Dev/DefaultFiles/ModelPred/ModelPredBase.json'


        filesave_from = FOLDER_DIR + '/Dev/DefaultFiles/FileSave/FileSaveBase.json'
        filesave_s3_key = f"data/Dev/ToDo/{username}/FileSave/{workfile_name}.json"
        s3.upload_file(filesave_from, R2_BUCKET, filesave_s3_key)
        logging.info(f"Uploaded FileSave JSON to s3://{R2_BUCKET}/{filesave_s3_key}")


        filesave_tbl_filepath = f"{filesave_s3_key}"
        modelpred_tbl_filepath = f"{modelpred_s3_key}"
        audio_tbl_filepath    = f"{s3_key}"
        logging.info(f"Moved")

        # If identical workfile name for user already exists then delete all these previous identical workfiles
        # delete_sql = text ("""
        #     DELETE FROM AA_IAP_WORKFILE 
        #     WHERE WORKFILE_NAME = :workfile_name AND ASSIGNED_USER_ID = :assigned_user
        # """)

        # session.execute(
        #     delete_sql,
        #     {
        #         'workfile_name': workfile_name + ".WKFL",
        #         'assigned_user': assigned_user
        #     }
        # )
        # session.commit()

        # Create new workfile row using SQL parameters
        insert_sql = text("""
            INSERT INTO AA_IAP_WORKFILE 
            VALUES
            (:workfile_tbl,  :audio_key, 'ToDo', :assigned_user, :audio_tbl_filepath, 'ToDo', :filesave_tbl_filepath,
                            'ToDo', :modelpred_tbl_filepath, 'ToDo', :now, 'ETL', 'Y', 'L3' ,'N', :cc_audio_id, :aa_record_id, :test_static_id, NULL, NULL, NULL,1,NULL,NULL);
        """)
        logging.info(f"query excusated")


        #Update AA_RECORD_RELATIONSHIPS with workfile_id
        update_aa_record_relationships_query = text("""UPDATE AA_RECORD_RELATIONSHIPS SET L3_WORKFILE_ID = :l3_workfile_id WHERE L4_AA_RECORD_ID = :l4_aa_record_id""")
        session.execute(update_aa_record_relationships_query,
                            {
                                'l3_workfile_id': workfile_id,
                                'l4_aa_record_id': row['ID']
                            })

        query_get_audio_key = f"SELECT AUDIO_KEY FROM AA_IAP_WORKFILE WHERE ID = '{row['WORKFILE_ID']}'"
        audio_key = str(conn.execute(text(query_get_audio_key)).scalar())
        
        now = datetime.now() 

        # Execute the INSERT statement
        result = session.execute(
            insert_sql,
            {
                'workfile_tbl': workfile_name + ".WKFL",
                'audio_key': 'obsolete',
                'assigned_user': assigned_user,
                'audio_tbl_filepath': audio_tbl_filepath,
                'filesave_tbl_filepath': filesave_tbl_filepath,
                'modelpred_tbl_filepath': modelpred_tbl_filepath,
                'now': now.strftime("%Y/%m/%d %H:%M:%S"),
                'cc_audio_id': None,
                'aa_record_id': row['ID'],
                'test_static_id': None,
                
            }
        )
        session.commit()





def process_splitting_auto_segment(session, aa_record_ids):
    query = f"""
        SELECT A.ID, B.WORKFILE_NAME, A.USER_ID, A.SEGMENT_START_TIME, A.SEGMENT_END_TIME, B.AUDIO_KEY
        FROM AA_RECORD A
        INNER JOIN AA_IAP_WORKFILE B on A.WORKFILE_ID = B.ID
        WHERE A.ID = {aa_record_ids} 
    """
    df = pd.read_sql(query, session.bind)

    for index, row in df.iterrows():
        try:
            # if row['WORKFILE_STATUS']!="Completed":
            audio_key_as_workfile_id =re.sub(r'_part\d+', '', row['WORKFILE_NAME'].split('.')[0]) #row['WORKFILE_NAME'].split('.')[0]
            if not audio_key_as_workfile_id:
                continue    

            segment_start_time = float(row['SEGMENT_START_TIME'])
            segment_end_time = float(row['SEGMENT_END_TIME'])
            record_id = row['ID']

            source_key = f"data/Dev/RawAudioFiles/ConvertedAudio/wav/{audio_key_as_workfile_id}.wav"
            dest_key = f"data/Dev/AA_RECORD/L3_{record_id}.wav"
            print(f"audio_key_as_workfile_id {audio_key_as_workfile_id}")

            # --- Download source file from R2 to temp file ---
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_source:
                s3.download_file(HMS_R2_BUCKET, source_key, tmp_source.name)
                tmp_source_path = tmp_source.name

            # --- Process audio ---
            file_to_export = GetSplittedAudio(tmp_source_path, segment_start_time, segment_end_time)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_dest:
                file_to_export.export(tmp_dest.name, format="wav")
                tmp_dest_path = tmp_dest.name
                uploaded = upload_if_not_exists(s3, HMS_R2_BUCKET, tmp_dest_path, dest_key)
                print(f'updaloade {uploaded}')
                if uploaded:
                    print(f"file uploaded to R2: {dest_key}")
                else:
                    print(f"error uploading to R2: {dest_key}")

                #     update_workfile_sql = text(f"""
                #     UPDATE AA_IAP_WORKFILE
                #     SET IS_FILE_UPLOAD = 1, 
                #     WHERE ID = :workfile_id
                # """)
                #     session.execute(update_workfile_sql, {
                #         "workfile_id": audio_key_as_workfile_id,
                #     })
                    # --- Upload processed file back to R2 ---
                # s3.upload_file(tmp_dest_path, HMS_R2_BUCKET, dest_key)
                # print(f"file uploaded to R2: {dest_key}")

            # --- Update DB ---
            update_sql = text(f"""
                UPDATE AA_RECORD
                SET SPLIT_L4_STATUS ='Running'
                WHERE ID = :record_id
            """)
            session.execute(update_sql, {
                "record_id": record_id
            })

            # --- Validate uploaded file ---
            if not is_valid_audio(tmp_dest_path):
                print(f"Invalid audio: {dest_key}")
                continue  

            # --- Insert new workfile mapping ---
            max_id_df = pd.read_sql("SELECT MAX(ID) AS max_id FROM AA_IAP_WORKFILE", session.bind)
            new_id = int(max_id_df.iloc[0]['max_id']) + 1

            insert_sql = text("""
                INSERT INTO aa_workfile_master (PARENT_AA_RECORD_ID, REVIEW_WORKFILE_ID)
                VALUES (:PARENT_AA_RECORD_ID, :REVIEW_WORKFILE_ID)
            """)
            session.execute(insert_sql, {
                'PARENT_AA_RECORD_ID': str(record_id),
                'REVIEW_WORKFILE_ID': str(new_id),
            })

            session.commit()

            # cleanup local temp files
            os.remove(tmp_source_path)
            os.remove(tmp_dest_path)

        except Exception as e:
            print(f"unable to process record: {row['ID']} with exception: {e}")

    
    def upload_if_not_exists(s3, bucket, local_path, s3_key):
    try:
        # Check if file already exists
        s3.head_object(Bucket=bucket, Key=s3_key)
        return False
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "404":
            # Not found → upload it
            s3.upload_file(local_path, bucket, s3_key)
            return True
        else:
            raise  # real error, re-raise