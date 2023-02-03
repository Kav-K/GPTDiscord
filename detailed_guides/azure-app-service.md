![image](https://user-images.githubusercontent.com/39274208/216566367-e1311c9e-bbc1-42fe-9e09-69e9fb728133.png)

You can choose to host the docker contained on app service so you have less maintenance, and it is also cheaper than a VM. 
As a student, you can get free Azure credits and you can get free Azure credits with you MSDN license if you work at a Microsoft partner.
The steps are also fairly straightforward.

To make it work on Azure App service follow the following steps: 

![image](https://user-images.githubusercontent.com/39274208/216566481-e06ccf0d-7346-438b-ab5a-7cd4ac6b08f5.png)


Creating the container: 

1. Sign into Azure via portal.azure.com
2. Search "app services" and click on it
3. choose resource group, webapp name.
4. At "publish" choose: "docker container"
5. OS: linux
6. Region: whichever is closer to you
7. choose Basic B1 as app service plan. this will cost you around 10 dollar in credits
8. At the "docker tab "change image source" to "dockerhub"
9. At image, use the following: kaveenk/gpt3discord:latest_release
10. Go to "review and create" and create the container! 
11. Wait a minute until it gets provisioned. 

![image](https://user-images.githubusercontent.com/39274208/216566567-a00bd5c1-1ab4-4250-a6d6-9ab9d872bd31.png)

Configurating the container:

App services makes it easy for you to add ENV files without using the CLI. We do this first

1. Go to "configuration" under "settings"
Enter the following env files as defined in the readme.md file in this repository: 

The most important application settings are:
ADMIN_ROLES
ALLOWED_GUILDS (your discord server token! not to be confused with a channel token or bot token)
DALLE_ROLES
DEBUG_CHANNEL
DEBUG_GUILD
DISCORD_TOKEN
GPT_ROLES
OPENAI_TOKEN
HEALTH_SERVICE_ENABLED

Health 
 1. It's important to also add WEBSITES_PORT to the application settings, since we are using the port for the health check of the container. Use 8181.
 2. Go to "Health check" under monitoring and enter the following value: /healthz
 3. Of course, set health check to: "on"
 4. Press save.

Last step: go to "deployment center" and turn off "continious deployment". 
This will make sure we get the latest build from docker hub when we restart the container. 

It's as easy as this. Of course, make sure to follow the other steps settings up the discord bot in the readme.md.

Hope this helps! 
