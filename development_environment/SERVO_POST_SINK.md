# Servo Manufacturing Results Submission

The Diagnose Me server includes a feature to post servo manufacturing results to a web server upon completion of the provisioning process. This document explains how to configure and test this feature.

## Configuration

The `server` container uses the `RESULTS_SERVER` environment variable to determine where to post results. This is configured in the `docker-compose.yaml` file:

```yaml
  server:
    ...
    environment:
      - TEST_DUT=brya
      - TEST_MODEL=banshee
      - RESULTS_SERVER=${RESULTS_SERVER:-}
```

To enable this feature, set the `RESULTS_SERVER` environment variable to your web server's URL before starting the services:

```bash
export RESULTS_SERVER=http://192.168.100.1:8080/
docker compose -f development_environment/docker-compose.yaml up -d
```

## Results Payload Format

The results are sent as a JSON POST request with the following structure:

```json
{
  "serial_number": "...",
  "mac_address": "...",
  "host_programming": {
    "success": true,
    "retry_count": 0,
    "failure_logs": []
  },
  "serial_programming": {
    "success": true,
    "retry_count": 0,
    "failure_logs": []
  },
  "dut_programming": {
    "success": true,
    "retry_count": 0,
    "failure_logs": []
  },
  "functional_testing": {
    "success": true,
    "retry_count": 0,
    "failure_logs": []
  },
  "integration_testing": {
    "success": true,
    "retry_count": 0,
    "failure_logs": []
  }
}
```

## Testing with the Mock Sink

A mock results sink is provided in the `development_environment/mock_sink` directory for testing purposes. It is a simple Flask server that logs incoming POST requests to stdout.

### Build the Mock Sink Image

```bash
docker build -t mock-sink development_environment/mock_sink
```

### Run the Mock Sink Container

To test the results submission feature, start the mock sink and point the `RESULTS_SERVER` variable to it.

```bash
# Start the mock sink (assuming it will run on port 8080)
docker run -p 8080:8080 --name mock-sink mock-sink
```

### Configure Diagnose Me to use the Mock Sink

Since the `server` container in `docker-compose.yaml` is configured with `network_mode: host`, it can directly access services running on the host machine using `localhost`.

```bash
export RESULTS_SERVER=http://localhost:8080/
docker compose -f development_environment/docker-compose.yaml up -d
```
