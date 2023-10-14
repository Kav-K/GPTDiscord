import discord
from discord.ext import tasks
from random import shuffle

class activity_service_cog(discord.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.activity_phrases = [
            # All activities except 'playing' have a ranging ~28 character limit.
            
            # Playing Activities
            (discord.ActivityType.playing, "engaging in cerebral chess duels", discord.Status.online),
            (discord.ActivityType.playing, "navigating epic quests in story-driven games", discord.Status.online),
            (discord.ActivityType.playing, "deciphering labyrinthine math enigmas", discord.Status.idle),
            (discord.ActivityType.playing, "weaving complex worlds of imagination", discord.Status.dnd),
            (discord.ActivityType.playing, "delving into cryptic ancient lore", discord.Status.idle),
            (discord.ActivityType.playing, "manipulating quantum phenomena in simulations", discord.Status.online),
            (discord.ActivityType.playing, "rivaling in lexical skirmishes in Scrabble", discord.Status.dnd),
            (discord.ActivityType.playing, "conducting war-games on strategic boards", discord.Status.online),
            (discord.ActivityType.playing, "unearthing clues in historical enigmas", discord.Status.idle),
            (discord.ActivityType.playing, "navigating existential labyrinths", discord.Status.online),
            (discord.ActivityType.playing, "conducting sociological experiments", discord.Status.idle),
            (discord.ActivityType.playing, "assembling cosmic puzzles", discord.Status.dnd),
            (discord.ActivityType.playing, "immersing in literary journeys", discord.Status.online),
            (discord.ActivityType.playing, "exploring virtual art galleries", discord.Status.idle),
            (discord.ActivityType.playing, "navigating ethical quandaries", discord.Status.online),
            (discord.ActivityType.playing, "virtual archaeological digs", discord.Status.idle),
            (discord.ActivityType.playing, "VR space exploration", discord.Status.dnd),
            (discord.ActivityType.playing, "embarking on time-bending narrative games", discord.Status.online),
            (discord.ActivityType.playing, "decoding encryption challenges", discord.Status.idle),

            # Competing Activities
            (discord.ActivityType.competing, "cerebral trivia competitions", discord.Status.online),
            (discord.ActivityType.competing, "engaging in battles of intellect", discord.Status.online),
            (discord.ActivityType.competing, "unravel cosmic secrets", discord.Status.idle),
            (discord.ActivityType.competing, "participating in mental decathlons", discord.Status.dnd),
            (discord.ActivityType.competing, "undertaking quests of metaphysical caliber", discord.Status.online),
            (discord.ActivityType.competing, "competing in code wizardry contests", discord.Status.idle),
            (discord.ActivityType.competing, "engaging in grandmaster chess bouts", discord.Status.dnd),
            (discord.ActivityType.competing, "participating in forensic debate contests", discord.Status.online),
            (discord.ActivityType.competing, "competing in allegorical riddle races", discord.Status.online),
            (discord.ActivityType.competing, "striving for quantum equation mastery", discord.Status.idle),
            (discord.ActivityType.competing, "participating in culinary arts showdowns", discord.Status.dnd),
            (discord.ActivityType.competing, "vying for poetic eloquence", discord.Status.online),
            (discord.ActivityType.competing, "engaging in interdisciplinary trivia", discord.Status.dnd),
            (discord.ActivityType.competing, "participating in virtual geo-politics", discord.Status.dnd),
            (discord.ActivityType.competing, "critiquing surreal art", discord.Status.online),
            (discord.ActivityType.competing, "participating in transdisciplinary hackathons", discord.Status.idle),
            (discord.ActivityType.competing, "engaging in narratology debates", discord.Status.online),
            (discord.ActivityType.competing, "striving for quantum cryptography", discord.Status.dnd),

            # Watching Activities limited to 29 char
            (discord.ActivityType.watching, "cosmic ballet in planetariums", discord.Status.idle),
            (discord.ActivityType.watching, "atomic kinetics unfold", discord.Status.online),
            (discord.ActivityType.watching, "TED discourses", discord.Status.dnd),
            (discord.ActivityType.watching, "minuscule realms in action", discord.Status.online),
            (discord.ActivityType.watching, "societal documentaries", discord.Status.idle),
            (discord.ActivityType.watching, "Aurora Borealis waltz", discord.Status.online),
            (discord.ActivityType.watching, "national park wildlife", discord.Status.dnd),
            (discord.ActivityType.watching, "dramatic arts", discord.Status.online),
            (discord.ActivityType.watching, "interstellar phenomena", discord.Status.idle),
            (discord.ActivityType.watching, "geopolitical machinations", discord.Status.online),
            (discord.ActivityType.watching, "vivid watercolor skies", discord.Status.dnd),
            (discord.ActivityType.watching, "cognitive dissonance", discord.Status.online),
            (discord.ActivityType.watching, "human behavior", discord.Status.idle),
            (discord.ActivityType.watching, "AI evolution", discord.Status.idle),
            (discord.ActivityType.watching, "critical theory debates", discord.Status.online),
            (discord.ActivityType.watching, "deep-sea bioluminescence", discord.Status.dnd),
            (discord.ActivityType.watching, "time-lapse of civilizations", discord.Status.online),
            (discord.ActivityType.watching, "nano-technological marvels", discord.Status.idle),


            # Listening Activities limited to 24 char
            (discord.ActivityType.listening, "orchestral epics", discord.Status.online),
            (discord.ActivityType.listening, "nature's subtle symphony", discord.Status.idle),
            (discord.ActivityType.listening, "dialectic exchanges", discord.Status.online),
            (discord.ActivityType.listening, "urban symphonies", discord.Status.dnd),
            (discord.ActivityType.listening, "wisdom from sages of yore", discord.Status.dnd),
            (discord.ActivityType.listening, "jazz virtuosos", discord.Status.idle),
            (discord.ActivityType.listening, "global music tapestries", discord.Status.online),
            (discord.ActivityType.listening, "spiritual reverberations", discord.Status.dnd),
            (discord.ActivityType.listening, "existentialist dialogues", discord.Status.online),
            (discord.ActivityType.listening, "empirical debates", discord.Status.idle),
            (discord.ActivityType.listening, "avant-garde compositions", discord.Status.online),
            (discord.ActivityType.listening, "abstract mathematical theorems", discord.Status.dnd),
            (discord.ActivityType.listening, "polyrhythmic soundscapes", discord.Status.idle),
            (discord.ActivityType.listening, "synthetic musical textures", discord.Status.dnd),
            (discord.ActivityType.listening, "posthumanist theory discussions", discord.Status.online),
            (discord.ActivityType.listening, "retrofuturistic podcasts", discord.Status.idle),
            (discord.ActivityType.listening, "tectonic movements", discord.Status.online),
            (discord.ActivityType.listening, "eclectic radio signals", discord.Status.dnd),

            # Streaming Activity
            {'type': 'streaming', 'url': 'https://twitch.tv/yourchannel', 'platform': 'Twitch'},

            # Additional Activities
            (discord.ActivityType.playing, "virtual historical riddles", discord.Status.online),
            (discord.ActivityType.playing, "cognitive gameplay trials", discord.Status.idle),
            (discord.ActivityType.playing, "navigating virtual journeys", discord.Status.online),
            (discord.ActivityType.playing, "voyaging through the mind's corridors", discord.Status.dnd),
            (discord.ActivityType.competing, " a mock UN assembly", discord.Status.online),
            (discord.ActivityType.watching, "global festival spectacles", discord.Status.idle),
            (discord.ActivityType.listening, "echoes of antiquity", discord.Status.online)

        ]
        
        self.status_options = [
            discord.Status.online,
            discord.Status.idle,
            discord.Status.dnd,
            discord.Status.invisible,
        ]
        
        self.default_status = discord.Status.online
        self.last_status = self.default_status
        self.shuffled_activity_phrases = self.activity_phrases.copy()
        shuffle(self.shuffled_activity_phrases)
        self.activity_index = 0

        # Initialize the activity update loop
        self.update_activity.start()

    async def change_activity(self):
        if self.activity_index == 0:
            shuffle(self.shuffled_activity_phrases)

        activity_config = self.shuffled_activity_phrases[self.activity_index]

        if isinstance(activity_config, dict):
            activity = discord.Streaming(
                name='Random',  # discord seems to ignore it and every other variable
                url=activity_config.get('url'),
                platform=activity_config.get('platform')
            )
        elif isinstance(activity_config, tuple):
            activity = discord.Activity(
                type=activity_config[0],
                name=activity_config[1]
            )
        else:
            raise ValueError("Unsupported activity configuration type.")

       # Check if activity_config has a status (activity_config[2]), if not, default to discord.Status.online
        status_choice = activity_config[2] if len(activity_config) > 2 else self.default_status

        # Check if the status has changed before updating it
        if self.last_status != status_choice:
            self.last_status = status_choice
            await self.bot.change_presence(activity=activity, status=status_choice)
        else:
            await self.bot.change_presence(activity=activity)

        self.activity_index = (self.activity_index + 1) % len(self.shuffled_activity_phrases)
    
    #The rate limit for changing the bot's status is typically around 2 updates per minute per bot user. (30s)
    #The rate limit for changing the bot's activity is approximately 5 updates per minute per bot user. (15s)
    @tasks.loop(seconds=36)
    async def update_activity(self):
        await self.change_activity()