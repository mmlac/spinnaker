#!/usr/bin/python
#
# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import sys

from annotate_source import Annotator
from build_release import Builder
from generate_bom import BomGenerator
from refresh_source import Refresher

from spinnaker.run import check_run_quick


def __annotate_component(annotator, component):
  """Annotate the component's source but don't include it in the BOM.

  Returns:
    Tuple of ([VersionBump] Halyard version bump, [string] head commit hash)
  """
  annotator.path = component
  annotator.parse_git_tree()
  version_bump = annotator.tag_head()
  head_hash = annotator.get_head_commit()
  annotator.delete_unwanted_tags()
  return (version_bump, head_hash)

def __record_halyard_nightly_version(version_bump, head_hash, options):
  """Record the version and commit hash at which Halyard was built in a bucket.

  Assumes that gsutil is installed on the machine this script is run from.

  This function uses `gsutil rsync` to read the GCS file, changes it in-place,
  and then uses `gsutil rsync` to write the file again. `rsync` is eventually
  consistent, so running this script (or manually manipulating the GCS file)
  concurrently could likely result in file corruption. Don't parallelize this.
  """
  bucket_uri = options.hal_nightly_bucket_uri
  build_number = options.build_number
  local_bucket_name = os.path.basename(bucket_uri)
  # Copy all the bucket contents to local (-r) and get rid of extra stuff (-d).
  if not os.path.exists(local_bucket_name):
    os.mkdir(local_bucket_name)
  check_run_quick('gsutil rsync -r -d {remote_uri} {local_bucket}'
                  .format(remote_uri=bucket_uri, local_bucket=local_bucket_name))
  hal_version = version_bump.version_str.replace('version-', '')
  new_hal_nightly_entry = ('{version}-{build}: {commit}'
                           .format(version=hal_version, build=build_number, commit=head_hash))
  nightly_entry_file = '{0}/nightly-version-commits.yml'.format(local_bucket_name)
  with open(nightly_entry_file, 'a') as nef:
    nef.write('{0}\n'.format(new_hal_nightly_entry))
  # Now sync the local dir with the bucket again after the update.
  check_run_quick('gsutil rsync -r -d {local_bucket} {remote_uri}'
                  .format(remote_uri=bucket_uri, local_bucket=local_bucket_name))

def init_argument_parser(parser):
  parser.add_argument('--hal_nightly_bucket_uri', default='',
                      help='The URI of the bucket to record the version and commit at which we built Halyard.')
  # Don't need to init args for Annotator since BomGenerator extends it.
  BomGenerator.init_argument_parser(parser)
  Builder.init_argument_parser(parser)

def main():
  """Build a Spinnaker release to be validated by Citest.
  """
  parser = argparse.ArgumentParser()
  init_argument_parser(parser)
  options = parser.parse_args()

  annotator = Annotator(options)
  halyard_bump, halyard_head_hash = __annotate_component(annotator, 'halyard')

  bom_generator = BomGenerator(options)
  bom_generator.determine_and_tag_versions()
  if options.container_builder == 'gcb':
    bom_generator.write_container_builder_gcr_config()
  elif options.container_builder == 'docker':
    bom_generator.write_docker_version_files()
  else:
    raise NotImplementedError('container_builder="{0}"'
                              .format(options.container_builder))
  Builder.do_build(options, build_number=options.build_number,
                   container_builder=options.container_builder)
  # Load version information into memory and write BOM to disk. Don't publish yet.
  bom_generator.write_bom()
  bom_generator.publish_microservice_configs()
  __record_halyard_nightly_version(halyard_bump, halyard_head_hash, options)
  bom_generator.publish_boms()
  bom_generator.generate_changelog()


if __name__ == '__main__':
  sys.exit(main())
