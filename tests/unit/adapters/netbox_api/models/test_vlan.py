"""
(c) 2020 Network To Code

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
  http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import os
import yaml

import pynetbox

from network_importer.adapters.netbox_api.models import NetboxVlan

ROOT = os.path.abspath(os.path.dirname(__file__))
FIXTURE_28 = "../fixtures/netbox_28"


def test_vlan_create_from_pynetbox():

    data = yaml.safe_load(open(f"{ROOT}/{FIXTURE_28}/vlan_101_no_tag.json"))
    pnb = pynetbox.core.response.Record(data, "http://mock", 1)

    item = NetboxVlan.create_from_pynetbox(pnb, "nyc")

    assert isinstance(item, NetboxVlan) is True
    assert item.remote_id == 1
    assert item.vid == 101
    assert item.associated_devices == []


def test_vlan_create_from_pynetbox_with_tags():

    data = yaml.safe_load(open(f"{ROOT}/{FIXTURE_28}/vlan_101_tags_01.json"))
    pnb = pynetbox.core.response.Record(data, "http://mock", 1)

    item = NetboxVlan.create_from_pynetbox(pnb, "nyc")

    assert isinstance(item, NetboxVlan) is True
    assert item.remote_id == 1
    assert item.vid == 101
    assert item.associated_devices == ["devA", "devB"]
