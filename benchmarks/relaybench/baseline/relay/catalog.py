class LocalClient:
    def __init__(self, rows):
        self.rows = dict(rows)

    def get(self, key):
        return self.rows.get(key)


def names_for(client, keys):
    return [client.get(key) for key in keys]


def delivery_windows():
    return [(day, slot) for day in range(7) for slot in range(3)]
