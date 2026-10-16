import json
import network
import ntptime
import time

from lib.drivers.registry import create_component
from lib.outbox import Outbox
from lib.transport import fetch_config, send_readings


CONFIG_FILE = "config.json"
DEVICE_STATE_FILE = "device_state.json"
OUTBOX_FILE = "pending_readings.json"
MAX_PENDING_READINGS = 500
MAX_BATCH_READINGS = 100
BATCH_INTERVAL_MS = 10000
CONFIG_REFRESH_MS = 300000
WIFI_RETRY_MS = 15000
NTP_SYNC_INTERVAL_MS = 3600000


def load_json(path):
    with open(path, "r") as file:
        return json.load(file)


def save_json(path, value):
    temporary_path = path + ".tmp"
    with open(temporary_path, "w") as file:
        json.dump(value, file)
    try:
        import os
        os.remove(path)
    except OSError:
        pass
    import os
    os.rename(temporary_path, path)


def connect_wifi(settings, station):
    if station.isconnected():
        return True

    if station.active():
        station.active(False)
        time.sleep_ms(100)
    station.active(True)
    time.sleep_ms(100)
    station.connect(settings["wifi"]["ssid"], settings["wifi"].get("password", ""))
    deadline = time.ticks_add(time.ticks_ms(), 15000)
    while not station.isconnected() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        time.sleep_ms(250)
    connected = station.isconnected()
    if not connected:
        print("Wi-Fi connection timed out; status:", station.status())
    return connected


def synchronize_clock():
    try:
        ntptime.settime()
        return True
    except Exception as error:
        print("NTP sync failed:", error)
        return False


def build_components(remote_config, settings):
    components = []
    reporting = settings.get("componentReporting", {})
    for definition in remote_config["components"]:
        try:
            components.append(create_component(definition, reporting))
        except (KeyError, TypeError, ValueError) as error:
            print("Skipping invalid component", definition.get("key", "?"), error)
    component_keys = {component["key"] for component in components}
    for component in components:
        valid_triggers = []
        for trigger in component["capture_on"]:
            source_key = trigger.get("component")
            if source_key not in component_keys:
                print(
                    "Ignoring capture trigger for",
                    component["key"],
                    "with unknown component key:",
                    source_key
                )
                continue
            valid_triggers.append(trigger)
        component["capture_on"] = valid_triggers
    return components


def load_saved_device_config():
    try:
        return load_json(DEVICE_STATE_FILE)
    except (OSError, ValueError):
        return None


def refresh_device_config(settings, saved_config):
    try:
        remote = fetch_config(
            settings["apiUrl"],
            settings["deviceCode"],
            settings.get("apiKey"),
        )
        if "configVersion" not in remote or "components" not in remote:
            raise ValueError("Backend returned an invalid device configuration")
        if saved_config is None or remote["configVersion"] != saved_config.get("configVersion"):
            save_json(DEVICE_STATE_FILE, remote)
            print("Saved backend device config version", remote["configVersion"])
        return remote
    except Exception as error:
        print("Using saved device config; refresh failed:", error)
        if saved_config is not None:
            return saved_config
        raise


def append_report(outbox, component, reading, urgent=False):
    reading["component"] = component["key"]
    outbox.append(reading, urgent)


def numeric_capture(component, purpose):
    values = []
    out_of_range_samples = 0
    for index in range(5):
        try:
            value, sample_quality = component["driver"].read()
        except Exception as error:
            print("Close measurement failed:", component["key"], error)
            value, sample_quality = None, "ERROR"
        if sample_quality != "ERROR" and value is not None:
            values.append(float(value))
            if sample_quality == "OUT_OF_RANGE":
                out_of_range_samples += 1
        if index < 4:
            time.sleep_ms(50)

    if not values:
        return component["reporter"].force_reading(None, "ERROR", time.ticks_ms(), purpose)

    measured = sorted(values)[len(values) // 2]
    max_distance_cm = component["reporter"].max_distance_cm
    quality = (
        "OUT_OF_RANGE"
        if (
            (max_distance_cm is not None and measured > max_distance_cm) or
            out_of_range_samples >= 3
        )
        else "OK"
    )
    return component["reporter"].force_reading(
        measured,
        quality,
        time.ticks_ms(),
        purpose
    )


def schedule_captures(components, source_key, value, now_ms):
    scheduled = []
    for source in components:
        for trigger in source["capture_on"]:
            if trigger.get("component") == source_key and trigger.get("value") is value:
                scheduled.append({
                    "target": source,
                    "due_ms": time.ticks_add(
                        now_ms,
                        int(trigger.get("delayS", 2)) * 1000
                    ),
                    "purpose": trigger.get("purpose", "DOOR_CLOSE")
                })
    return scheduled


def sample_components(components, outbox, captures, now_ms):
    for capture in captures[:]:
        if time.ticks_diff(now_ms, capture["due_ms"]) >= 0:
            reading = numeric_capture(capture["target"], capture["purpose"])
            append_report(outbox, capture["target"], reading, True)
            captures.remove(capture)

    for component in components:
        if component["value_kind"] == "NUMBER":
            interval_ms = 1000
        else:
            interval_ms = component["interval_ms"]

        last_sample = component["last_sample_ms"]
        if last_sample is not None and time.ticks_diff(now_ms, last_sample) < interval_ms:
            continue
        component["last_sample_ms"] = now_ms

        try:
            value, quality = component["driver"].read()
        except Exception as error:
            print("Sensor read failed:", component["key"], error)
            value, quality = None, "ERROR"

        reading, changed = component["reporter"].observe(value, quality, now_ms)
        if reading is None:
            continue
        append_report(
            outbox,
            component,
            reading,
            component["value_kind"] == "BOOLEAN" and changed
        )
        if component["value_kind"] == "BOOLEAN" and changed:
            captures.extend(schedule_captures(
                components,
                component["key"],
                value,
                now_ms
            ))


def main():
    settings = load_json(CONFIG_FILE)
    api_url = settings["apiUrl"].rstrip("/")
    settings["apiUrl"] = api_url
    station = network.WLAN(network.STA_IF)
    saved_config = load_saved_device_config()
    outbox = Outbox(OUTBOX_FILE, MAX_PENDING_READINGS)
    components = build_components(saved_config, settings) if saved_config else []
    captures = []
    last_config_refresh = time.ticks_add(time.ticks_ms(), -CONFIG_REFRESH_MS)
    last_batch = time.ticks_ms()
    last_wifi_attempt = time.ticks_add(last_batch, -WIFI_RETRY_MS)
    last_ntp_attempt = time.ticks_add(last_batch, -NTP_SYNC_INTERVAL_MS)
    clock_synchronized = False

    while True:
        now_ms = time.ticks_ms()
        if not station.isconnected() and time.ticks_diff(now_ms, last_wifi_attempt) >= WIFI_RETRY_MS:
            last_wifi_attempt = now_ms
            try:
                print("Connecting to Wi-Fi...")
                if connect_wifi(settings, station):
                    print("Wi-Fi connected:", station.ifconfig()[0])
                    clock_synchronized = synchronize_clock()
                    last_ntp_attempt = time.ticks_ms()
            except Exception as error:
                print("Wi-Fi connection failed:", error)

        if station.isconnected():
            ntp_interval = NTP_SYNC_INTERVAL_MS if clock_synchronized else 60000
            if time.ticks_diff(now_ms, last_ntp_attempt) >= ntp_interval:
                clock_synchronized = synchronize_clock()
                last_ntp_attempt = time.ticks_ms()

            if time.ticks_diff(now_ms, last_config_refresh) >= CONFIG_REFRESH_MS:
                last_config_refresh = now_ms
                try:
                    remote = refresh_device_config(settings, saved_config)
                    if saved_config is None or remote.get("configVersion") != saved_config.get("configVersion"):
                        components = build_components(remote, settings)
                        saved_config = remote
                except Exception as error:
                    print("Device config unavailable:", error)

        sample_components(components, outbox, captures, now_ms)
        batch_due = time.ticks_diff(now_ms, last_batch) >= BATCH_INTERVAL_MS
        if station.isconnected() and outbox.entries and (batch_due or outbox.has_urgent()):
            entries = outbox.batch(MAX_BATCH_READINGS)
            payload = {
                "deviceCode": settings["deviceCode"],
                "firmware": settings.get("firmwareVersion", "0.1.0"),
                "readings": [entry["reading"] for entry in entries],
            }
            try:
                result = send_readings(api_url, settings.get("apiKey"), payload)
                rejected = result.get("rejected", [])
                discarded = result.get("discarded", [])
                completed = result.get("accepted", 0) + len(rejected) + len(discarded)
                if completed != len(entries):
                    raise ValueError("Ingestion response does not account for the submitted batch")
                print("Readings accepted:", result.get("accepted", 0))
                for item in rejected:
                    print("Reading rejected:", item.get("component"), item.get("reason"))
                for item in discarded:
                    print("Duplicate reading discarded:", item.get("component"), item.get("reason"))
                outbox.acknowledge(len(entries))
            except Exception as error:
                print("Ingestion failed; keeping readings in persistent outbox:", error)
            last_batch = time.ticks_ms()

        time.sleep_ms(50)


if __name__ == "__main__":
    main()
