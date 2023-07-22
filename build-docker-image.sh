#!/bin/sh

# Ask the user for the image name
read -p "Enter image name: " image_name

# Run the docker build command
docker build -t "$image_name" .
