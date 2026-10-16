from machine import Pin

from .debounce import DebouncedDigitalInput


class MC38:
    def __init__(self, config):
        self.pin = Pin(config["signalPin"], Pin.IN, Pin.PULL_UP)
        self.debounced_input = DebouncedDigitalInput(self.pin, config, 0)

    def read(self):
        return self.debounced_input.read(), "OK"
