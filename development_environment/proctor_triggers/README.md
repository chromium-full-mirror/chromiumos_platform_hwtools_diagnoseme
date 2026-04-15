# Proctor Triggers

This directory contains the Proctor configuration files used to trigger Cloud Build pipelines for the `diagnoseme` repository on Gerrit events.

## Deploying or Updating a Trigger

To create or update a Proctor trigger, use the `stubby` command to push the configuration to the `ProctorMetadataService`.

### 1. Generate a Mint Token
You first need to generate a valid LOAS mint token with the correct scopes (35600) to authenticate the RPC call.

```bash
/google/data/ro/projects/gaiamint/bin/get_mint --type=loas --out=/tmp/mint.txt --scopes=35600 --endusercreds
```

### 2. Submit the Configuration
Pipe the configuration file (e.g., `diagnoseme-cq`) into the `CreateTrigger` RPC method using `stubby`.

```bash
cat development_environment/proctor_triggers/diagnoseme-cq | stubby call --rpc_creds_file=/tmp/mint.txt blade:alphasource-ci-proctor-metadata-service-prod-global ProctorMetadataService.CreateTrigger --proto2
```
