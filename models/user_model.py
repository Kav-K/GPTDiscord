"""
Store information about a discord user, for the purposes of enabling conversations. We store a message
history, message count, and the id of the user in order to track them.
"""


class RedoUser:
    def __init__(self, prompt, message, ctx, response):
        self.prompt = prompt
        self.message = message
        self.ctx = ctx
        self.response = response
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