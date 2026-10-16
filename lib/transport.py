import json


def _request(method, url, api_key, body=None):
    import urequests

    headers = {}
    if api_key:
        headers["x-device-key"] = api_key
    if body is not None:
        headers["Content-Type"] = "application/json"

    response = None
    try:
        if method == "GET":
            response = urequests.get(url, headers=headers)
        else:
            response = urequests.post(
                url,
                headers=headers,
                data=json.dumps(body),
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise OSError("HTTP " + str(response.status_code))
        return response.json()
    finally:
        if response is not None:
            response.close()


def fetch_config(api_url, device_code, api_key=None):
    url = api_url.rstrip("/") + "/api/devices/" + device_code + "/config"
    return _request("GET", url, api_key)


def send_readings(api_url, api_key, payload):
    url = api_url.rstrip("/") + "/api/ingest/v1/readings"
    return _request("POST", url, api_key, payload)
