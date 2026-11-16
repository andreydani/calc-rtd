class Recorder:
    """A hub client that just records what it is sent."""

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)

    def values(self, client_id):
        return [v for m in self.messages if m["op"] == "update"
                for i, v in m["values"] if i == client_id]

    def errors(self):
        return [m for m in self.messages if m["op"] == "error"]


class FakeDriver:
    """Scriptable stand-in for com_driver.ComDriver."""

    instances = []

    def __init__(self, progid, server=""):
        self.progid, self.server = progid, server
        self.notified = False
        self.heartbeat_interval = 15.0
        self.start_result = 1
        self.alive = True
        self.connected = {}
        self.pending = []
        self.calls = []
        FakeDriver.instances.append(self)

    def start(self):
        self.calls.append("start")
        return self.start_result

    def connect_data(self, topic_id, strings):
        self.calls.append(("connect", topic_id, tuple(strings)))
        if strings[0] == "BOOM":
            raise RuntimeError("bad topic")
        self.connected[topic_id] = tuple(strings)
        return "init:" + strings[0]

    def refresh_data(self):
        self.calls.append("refresh")
        pending, self.pending = self.pending, []
        return pending

    def disconnect_data(self, topic_id):
        self.calls.append(("disconnect", topic_id))
        self.connected.pop(topic_id, None)

    def heartbeat(self):
        self.calls.append("heartbeat")
        return 1 if self.alive else 0

    def terminate(self):
        self.calls.append("terminate")

    def push(self, topic_id, value):
        self.pending.append((topic_id, value))
        self.notified = True
