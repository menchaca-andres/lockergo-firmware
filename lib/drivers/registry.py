from .fc51 import FC51
from .hcsr04 import HCSR04
from .mc38 import MC38
from ..reporter import Reporter


DRIVERS = {
    "fc51": FC51,
    "hcsr04": HCSR04,
    "mc38": MC38,
}


def create_component(component, reporting_overrides=None):
    driver_name = component["model"]["driver"]
    driver_type = DRIVERS.get(driver_name)
    if driver_type is None:
        raise ValueError("Unsupported component driver: " + driver_name)

    value_kind = component["model"]["type"]["valueKind"]
    if driver_name in ("fc51", "mc38") and value_kind != "BOOLEAN":
        raise ValueError("Digital switch drivers require BOOLEAN valueKind")
    if driver_name == "hcsr04" and value_kind != "NUMBER":
        raise ValueError("HC-SR04 requires NUMBER valueKind")

    reporter_config = (reporting_overrides or {}).get(component["key"], {})
    return {
        "key": component["key"],
        "interval_ms": max(50, int(component["config"].get("intervalMs", 1000))),
        "value_kind": value_kind,
        "driver": driver_type(component["config"]),
        "reporter": Reporter(value_kind, reporter_config),
        "capture_on": reporter_config.get("captureOn", []),
        "last_sample_ms": None,
    }
