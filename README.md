# data-ingestion

## Overview

This repository contains a solution for HCAO to OCR sign up sheets into Google Sheets.

The design / process flow for this is [here](documents/design.drawio.png)

## Requirements

* Google cloud cli
* hcao email address
* you must have been added to the google cloud project at least as an editor
* added as a member to the Google Shared Drive: https://drive.google.com/drive/folders/152xJTWCOuabDVqkTwWY8uKatK-Z77Y91?usp=drive_link

To test this manually, run:

`google auth login`

to login to google cloud. Then run:

`gcloud auth print-identity-token   --impersonate-service-account=ocr-test-caller@hcao-ocr-pipeline.iam.gserviceaccount.com   --audiences=https://ocr-signup-sheet-f4yhe5fcoa-uw.a.run.app` 

to get an auth token 

Open whatever tool you prefer to test APIs (I use Postman)

Create a POST to https://ocr-signup-sheet-f4yhe5fcoa-uw.a.run.app

Under headers, add these two headers:

1. Content-Type (application/json)
2. Authorization Bearer [paste in your auth token]

You'll need to add test files to the ingest folder here: https://drive.google.com/drive/folders/1hrpUcdFpjfTee-AS2LZINXJ8r2iQOw_e?usp=drive_link

Requirements for ingest:

* image files ONLY
* must be in correct orientation 
* do not create any subfolder structure 

To deploy the function:

Change directories to src/ocr-function and run:

```
 gcloud functions deploy ocr-signup-sheet  \
 --gen2 \
 --runtime=python312 \
 --region=us-west1 \
 --source=. \
 --entry-point=ocr_signup_sheet \
 --trigger-http \
 --timeout=300s \  
 --memory=512MB \  
 --service-account=ocr-drive-poller@hcao-ocr-pipeline.iam.gserviceaccount.com \  
 --set-secrets=MAKKYO_API_KEY=makkyo-api-key:latest
```

To create the scheduled job:

```
gcloud scheduler jobs create http ocr-drive-poll \
  --location=us-west1 \
  --schedule="*/15 * * * *" \
  --uri="https://ocr-signup-sheet-f4yhe5fcoa-uw.a.run.app" \
  --http-method=POST \
  --oidc-service-account-email=ocr-test-caller@hcao-ocr-pipeline.iam.gserviceaccount.com \
  --oidc-token-audience=https://ocr-signup-sheet-f4yhe5fcoa-uw.a.run.app \
  --attempt-deadline=300s
```

You can monitor the cloud job here: 

https://console.cloud.google.com/cloudscheduler?referrer=search&authuser=1&project=hcao-ocr-pipeline