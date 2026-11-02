#!/bin/sh
# Pushes the demo's images to the registry of inventory.yml, under the tag of inventory.yml (run on the build machine):
#   publish-images.sh <nebulastream checkout> <nebulastream-ui-SIGMOD-Demo checkout>
# The worker and CLI images must have been built in the NebulaStream checkout first, with the same tag:
#   NES_DOCKER_TAG=<tag> cmake --build <build dir> --target package-docker-nes-worker package-docker-nes-cli
# (inside its development container). The UI and generator images are built here, for arm64.
set -eu
cd "$(dirname "$0")"
[ $# -eq 2 ] || { sed -n 2,7p "$0"; exit 2; }
ui=$2
registry=$(sed -n 's/^ *registry: *\([^ ]*\).*/\1/p' inventory.yml)
tag=$(sed -n 's/^ *demo_tag: *\([^ ]*\).*/\1/p' inventory.yml)
for image in nes-worker nes-cli; do
    docker tag "nebulastream/$image:$tag" "$registry/$image:$tag"
    docker push -q "$registry/$image:$tag"
done
docker buildx build --platform linux/arm64 --push -t "$registry/sigmod-ui:$tag" "$ui/webui"
for generator in patient-monitor lab-values; do
    docker buildx build --platform linux/arm64 --push -t "$registry/sigmod-$generator:$tag" "../generators/$generator"
done
