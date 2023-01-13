"""
Store information about a discord user, for the purposes of enabling conversations. We store a message
history, message count, and the id of the user in order to track them.
"""


class RedoUser:
    def __init__(self, prompt, instruction, message, ctx, response, codex, paginator):
        self.prompt = prompt
        self.instruction = instruction
        self.message = message
        self.ctx = ctx
        self.response = response
        self.codex = codex
        self.paginator = paginator
        self.interactions = []

    def add_interaction(self, interaction):
        self.interactions.append(interaction)

    def in_interaction(self, interaction):
        return interaction in self.interactions

    # Represented by user_id
    def __hash__(self):
        return hash(self.message.author.id)

    def __eq__(self, other):
        return self.message.author.id == other.message.author.id

    # repr
    def __repr__(self):
        return f"RedoUser({self.message.author.id})"


class User:
    def __init__(self, id):
        self.id = id
        self.history = []
        self.count = 0

    # These user objects should be accessible by ID, for example if we had a bunch of user
    # objects in a list, and we did `if 1203910293001 in user_list`, it would return True
    # if the user with that ID was in the list
    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f"User(id={self.id}, history={self.history})"

    def __str__(self):
        return self.__repr__()


class Thread:
    def __init__(self, id):
        self.id = id
        self.history = []
        self.count = 0
        self.model = None
        self.temperature = None
        self.top_p = None
        self.frequency_penalty = None
        self.presence_penalty = None

    def set_overrides(
        self,
        temperature=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
    ):
        self.temperature = temperature
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty

    def get_overrides(self):
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
        }

    # These user objects should be accessible by ID, for example if we had a bunch of user
    # objects in a list, and we did `if 1203910293001 in user_list`, it would return True
    # if the user with that ID was in the list
    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f"Thread(id={self.id}, history={self.history})"

    def __str__(self):
        return self.__repr__()


class EmbeddedConversationItem:
    def __init__(self, text, timestamp):
        self.text = text
        self.timestamp = int(timestamp)

    def __repr__(self):
        return self.text

    def __str__(self):
        return self.__repr__()

    def __eq__(self, other):
        return self.text == other.text and self.timestamp == other.timestamp

    def __hash__(self):
        return hash(self.text) + hash(self.timestamp)

    def __lt__(self, other):
        return self.timestamp < other.timestamp

    def __gt__(self, other):
        return self.timestamp > other.timestamp

    def __le__(self, other):
        return self.timestamp <= other.timestamp

    def __ge__(self, other):
        return self.timestamp >= other.timestamp

    def __ne__(self, other):
        return not self.__eq__(other)

    # Make it such that if there is an arry with these EmbeddedConversationItems, if we "".join the array, each item will
    # return the .text attribute
    def __format__(self, format_spec):
        return self.text
