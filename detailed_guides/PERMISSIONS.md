### Permissions  
  
As mentioned in the comments of the sample environment file, there are three permission groups that you can edit in the environment (`.env`) file. `ADMIN_ROLES` are roles that allow users to use `/system` and `/mod` commands. `GPT_ROLES` are roles that allow users to use `/gpt` commands, and `DALLE_ROLES` are roles that allow users to use `/dalle` commands. `TRANSLATE_ROLES` allows users to use translation, `INDEX_ROLES` allow users to use custom indexing commands, and `SEARCH_ROLES` allows users to use the search functionality.  
  
  
If for a command group you want everybody to be able to use those commands, just don't include the relevant line in the `.env` file. For example, if you want everyone to be able to use GPT commands, you can just omit `the GPT_ROLES="...."` line.  