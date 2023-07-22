#!/bin/sh

# Ask the user for the image name
read -p "Enter the name of the image.: " image_name

# Execute the command 'docker run'
docker run -d -p 80:80 --name=my_app_container --restart=unless-stopped "$image_name"
