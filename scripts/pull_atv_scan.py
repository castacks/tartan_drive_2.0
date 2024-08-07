from minio import Minio
from minio.error import S3Error

import os


# Minio client configuration
access_key = "m7sTvsz28Oq3AicEDHFo"
secret_key = "YVPGh367RnrT7G33lG6DtbaeuFZCqTE6KabMQClw"
endpoint_url = "airlab-share-01.andrew.cmu.edu:9000"

minio_client = Minio(endpoint_url, access_key=access_key, secret_key=secret_key,secure=True, cert_check=False)

# Bucket name
bucket_name = 'tartandrive2'

minio_client.fget_object(bucket_name, 'vicky1_clean.pts', './assets/vicky1_clean.pts')
