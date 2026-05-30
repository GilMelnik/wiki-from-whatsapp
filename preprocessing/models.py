from datetime import datetime


class Message:
    def __init__(self, date_time, sender, content):
        self.datetime = date_time
        self.sender = sender
        self.content = content

    @classmethod
    def from_dict(cls, dict_data):
        return cls(
            date_time=datetime.fromisoformat(dict_data['datetime']),
            sender=dict_data['sender'],
            content=dict_data['content'],
        )
        
    def to_dict(self):
        return {
            "datetime": self.datetime.isoformat(),
            "sender": self.sender,
            "content": self.content
        }


class Conversation:
    def __init__(self, message=None):
        self.messages = list()
        if message:
            self.add_message(message)

    def add_message(self, message):
        self.messages.append(message)

    def get_last_messages(self, context_window=10):
        context = [message for message in self.messages[-context_window:]]
        return context

    def to_dict(self):
        output_dict = [message.to_dict() for message in self.messages]
        return output_dict


class IndexList:
    def __init__(self, max_size):
        self.max_size = max_size
        self.items = []

    def add(self, index):
        if index in self.items:
            self.items.remove(index)
        self.items.append(index)
        if len(self.items) > self.max_size:
            self.items.pop(0)

    def __iter__(self):
        # Iterate from last to first
        for item in reversed(self.items):
            yield item

    def __repr__(self):
        return f"IndexList({self.items})"

