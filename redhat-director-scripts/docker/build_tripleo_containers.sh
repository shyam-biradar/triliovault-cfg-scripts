#!/bin/bash

set -e

echo -e "\nPREREQUISITES:\n\tPlease make sure that you logged in to docker.io"
echo -e "\tdocker.io should be logged in with user having pull and push permissions to https://hub.docker.com/u/trilio/dashboard/"
echo -e "\tYou can use following commands:"
echo -e "\n\t- docker login docker.io\n"


if [ $# -lt 1 ];then
   echo "Script takes exacyly 1 argument"
   echo -e "./build_tripleo_containers.sh <tvault_version>"
   exit 1
fi

tvault_version=$1


current_dir=$(pwd)
base_dir="$(dirname $0)"

if [ $base_dir = '.' ]
then
base_dir="$current_dir"
fi

declare -a openstack_releases=("train")

## now loop through the above array
for openstack_release in "${openstack_releases[@]}"
do
      build_dir=tmp_docker_${openstack_release}
      rm -rf $base_dir/${build_dir}
      mkdir -p $base_dir/${build_dir}
      cp -R $base_dir/trilio-datamover $base_dir/${build_dir}/
      cp -R $base_dir/trilio-datamover-api $base_dir/${build_dir}/
      cp -R $base_dir/trilio-horizon-plugin $base_dir/${build_dir}/

      #Build trilio-datamover containers
      echo -e "Creating trilio-datamover container for ${openstack_release}"
      cd $base_dir/${build_dir}/trilio-datamover/
      cp Dockerfile_tripleo${openstack_release} Dockerfile
      docker build --no-cache -t docker.io/trilio/trilio-datamover-tripleo:${tvault_version}-${openstack_release} .
      podman push docker.io/trilio/trilio-datamover-tripleo:${tvault_version}-${openstack_release}


      #Build trilio_datamover-api containers
      echo -e "Creating trilio-datamover container-api for ${openstack_release}"
      cd $base_dir/${build_dir}/trilio-datamover-api/
      cp Dockerfile_tripleo${openstack_release} Dockerfile
      docker build --no-cache -t docker.io/trilio/trilio-datamover-api-tripleo:${tvault_version}-${openstack_release} .
      docker push docker.io/trilio/trilio-datamover-api-tripleo:${tvault_version}-${openstack_release}

      #Build trilio_horizon_plugin containers
      echo -e "Creating trilio-horizon-plugin container for ${openstack_release}"
      cd $base_dir/${build_dir}/trilio-horizon-plugin/
      cp Dockerfile_tripleo${openstack_release} Dockerfile
      docker build --no-cache -t docker.io/trilio/trilio-horizon-plugin-tripleo:${tvault_version}-${openstack_release} .
      docker push docker.io/trilio/trilio-horizon-plugin-tripleo:${tvault_version}-${openstack_release}

      # Clean the build_dir
      rm -rf $base_dir/${build_dir}

done