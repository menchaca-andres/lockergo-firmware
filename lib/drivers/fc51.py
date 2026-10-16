from machine import Pin

from .debounce import DebouncedDigitalInput


class FC51:
    def __init__(self, config):
        self.pin = Pin(config["signalPin"], Pin.IN)
        self.debounced_input = DebouncedDigitalInput(self.pin, config, 0)

    def read(self):
        return self.debounced_input.read(), "OK"
