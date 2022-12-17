import os

import openai

# An enum of two modes, TOP_P or TEMPERATURE
class Mode:
    TOP_P = "top_p"
    TEMPERATURE = "temperature"


class Models:
    DAVINCI = "text-davinci-003"
    CURIE = "text-curie-001"

class Model:
    def __init__(self, usage_service):
        self._mode = Mode.TEMPERATURE
        self._temp = 0.6  # Higher value means more random, lower value means more likely to be a coherent sentence
        self._top_p = 0.9  # 1 is equivalent to greedy sampling, 0.1 means that the model will only consider the top 10% of the probability distribution
        self._max_tokens = 4000  # The maximum number of tokens the model can generate
        self._presence_penalty = 0  # Penalize new tokens based on whether they appear in the text so far
        self._frequency_penalty = 0  # Penalize new tokens based on their existing frequency in the text so far. (Higher frequency = lower probability of being chosen.)
        self._best_of = 1  # Number of responses to compare the loglikelihoods of
        self._prompt_min_length = 20
        self._max_conversation_length = 5
        self._model = Models.DAVINCI
        self._low_usage_mode = False
        self.usage_service = usage_service
        self.DAVINCI_ROLES = ["admin", "Admin", "GPT", "gpt"]

        openai.api_key = os.getenv('OPENAI_TOKEN')

    # Use the @property and @setter decorators for all the self fields to provide value checking

    @property
    def low_usage_mode(self):
        return self._low_usage_mode

    @low_usage_mode.setter
    def low_usage_mode(self, value):
        try:
            value = bool(value)
        except ValueError:
            raise ValueError("low_usage_mode must be a boolean")

        if value:
            self._model = Models.CURIE
            self.max_tokens = 1900
        else:
            self._model = Models.DAVINCI
            self.max_tokens = 4000

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, model):
        if model not in [Models.DAVINCI, Models.CURIE]:
            raise ValueError("Invalid model, must be text-davinci-003 or text-curie-001")
        self._model = model

    @property
    def max_conversation_length(self):
        return self._max_conversation_length

    @max_conversation_length.setter
    def max_conversation_length(self, value):
        value = int(value)
        if value < 1:
            raise ValueError("Max conversation length must be greater than 1")
        if value > 30:
            raise ValueError("Max conversation length must be less than 30, this will start using credits quick.")
        self._max_conversation_length = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value not in [Mode.TOP_P, Mode.TEMPERATURE]:
            raise ValueError("mode must be either 'top_p' or 'temperature'")
        if value == Mode.TOP_P:
            self._top_p = 0.1
            self._temp = 0.7
        elif value == Mode.TEMPERATURE:
            self._top_p = 0.9
            self._temp = 0.6

        self._mode = value

    @property
    def temp(self):
        return self._temp

    @temp.setter
    def temp(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("temperature must be greater than 0 and less than 1, it is currently " + str(value))

        self._temp = value

    @property
    def top_p(self):
        return self._top_p

    @top_p.setter
    def top_p(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("top_p must be greater than 0 and less than 1, it is currently " + str(value))
        self._top_p = value

    @property
    def max_tokens(self):
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value):
        value = int(value)
        if value < 15 or value > 4096:
            raise ValueError("max_tokens must be greater than 15 and less than 4096, it is currently " + str(value))
        self._max_tokens = value

    @property
    def presence_penalty(self):
        return self._presence_penalty

    @presence_penalty.setter
    def presence_penalty(self, value):
        if int(value) < 0:
            raise ValueError("presence_penalty must be greater than 0, it is currently " + str(value))
        self._presence_penalty = value

    @property
    def frequency_penalty(self):
        return self._frequency_penalty

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        if int(value) < 0:
            raise ValueError("frequency_penalty must be greater than 0, it is currently " + str(value))
        self._frequency_penalty = value

    @property
    def best_of(self):
        return self._best_of

    @best_of.setter
    def best_of(self, value):
        value = int(value)
        if value < 1 or value > 3:
            raise ValueError(
                "best_of must be greater than 0 and ideally less than 3 to save tokens, it is currently " + str(value))
        self._best_of = value

    @property
    def prompt_min_length(self):
        return self._prompt_min_length

    @prompt_min_length.setter
    def prompt_min_length(self, value):
        value = int(value)
        if value < 10 or value > 4096:
            raise ValueError(
                "prompt_min_length must be greater than 10 and less than 4096, it is currently " + str(value))
        self._prompt_min_length = value

    def send_request(self, prompt, message):
        # Validate that  all the parameters are in a good state before we send the request
        if len(prompt) < self.prompt_min_length:
            raise ValueError("Prompt must be greater than 25 characters, it is currently " + str(len(prompt)))


        print("The prompt about to be sent is " + prompt)
        prompt_tokens = self.usage_service.count_tokens(prompt)
        print(f"The prompt tokens will be {prompt_tokens}")
        print(f"The total max tokens will then be {self.max_tokens - prompt_tokens}")

        response = openai.Completion.create(
            model=Models.DAVINCI if any(role.name in self.DAVINCI_ROLES for role in message.author.roles) else self.model, # Davinci override for admin users
            prompt=prompt,
            temperature=self.temp,
            top_p=self.top_p,
            max_tokens=self.max_tokens - prompt_tokens,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
            best_of=self.best_of,
        )
        print(response.__dict__)

        # Parse the total tokens used for this request and response pair from the response
        tokens_used = int(response['usage']['total_tokens'])
        self.usage_service.update_usage(tokens_used)

        return response