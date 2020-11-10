"""Extension of the base Models for the NetboxAPIAdapter.

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
from typing import Optional
import logging

import pynetbox
from diffsync.exceptions import ObjectNotFound
from diffsync import DiffSync, DiffSyncModel  # pylint: disable=unused-import

import network_importer.config as config  # pylint: disable=import-error
from network_importer.adapters.netbox_api.exceptions import NetboxObjectNotValid
from network_importer.models import (  # pylint: disable=import-error
    Site,
    Device,
    Interface,
    IPAddress,
    Cable,
    Prefix,
    Vlan,
)

LOGGER = logging.getLogger("network-importer")


class NetboxSite(Site):
    """Extension of the Site model."""

    remote_id: Optional[int]


class NetboxDevice(Device):
    """Extension of the Device model."""

    remote_id: Optional[int]
    primary_ip: Optional[str]


class NetboxInterface(Interface):
    """Extension of the Interface model."""

    remote_id: Optional[int]
    connected_endpoint_type: Optional[str]

    def translate_attrs_for_netbox(self, attrs):  # pylint: disable=too-many-branches
        """Translate interface parameters into Netbox format.

        Args:
            params (dict): Dictionnary of attributes/parameters of the object to translate

        Returns:
            dict: Netbox parameters
        """

        def convert_vlan_to_nid(vlan_uid):
            vlan = self.diffsync.get(self.diffsync.vlan, identifier=vlan_uid)
            if vlan:
                return vlan.remote_id
            return None

        nb_params = {}

        # Identify the id of the device this interface is attached to
        device = self.diffsync.get(self.diffsync.device, identifier=self.device_name)
        if not device:
            raise ObjectNotFound(f"device {self.device_name}, not found")

        if not device.remote_id:
            raise NetboxObjectNotValid(f"device {self.device_name}, is missing a remote_id")

        nb_params["device"] = device.remote_id
        nb_params["name"] = self.name

        if "is_lag" in attrs and attrs["is_lag"]:
            nb_params["type"] = "lag"
        elif "is_virtual" in attrs and attrs["is_virtual"]:
            nb_params["type"] = "virtual"
        else:
            nb_params["type"] = "other"

        if "mtu" in attrs:
            nb_params["mtu"] = attrs["mtu"]

        if "description" in attrs:
            nb_params["description"] = attrs["description"] or ""

        if "switchport_mode" in attrs and attrs["switchport_mode"] == "ACCESS":
            nb_params["mode"] = "access"
        elif "switchport_mode" in attrs and attrs["switchport_mode"] == "TRUNK":
            nb_params["mode"] = "tagged"

        # if is None:
        #     intf_properties["enabled"] = intf.active

        if config.SETTINGS.main.import_vlans not in [False, "no"]:
            if "mode" in attrs and attrs["mode"] in ["TRUNK", "ACCESS"] and attrs["access_vlan"]:
                nb_params["untagged_vlan"] = convert_vlan_to_nid(attrs["access_vlan"])
            elif "mode" in attrs and attrs["mode"] in ["TRUNK", "ACCESS"] and not attrs["access_vlan"]:
                nb_params["untagged_vlan"] = None

            if (
                "mode" in attrs
                and attrs["mode"] in ["TRUNK", "L3_SUB_VLAN"]
                and "allowed_vlans" in attrs
                and attrs["allowed_vlans"]
            ):
                nb_params["tagged_vlans"] = [convert_vlan_to_nid(vlan) for vlan in attrs["allowed_vlans"]]
            elif (
                "mode" in attrs
                and attrs["mode"] in ["TRUNK", "L3_SUB_VLAN"]
                and ("allowed_vlans" not in attrs or not attrs["allowed_vlans"])
            ):
                nb_params["tagged_vlans"] = []

        if "is_lag_member" in attrs and attrs["is_lag_member"]:
            parent_interface = self.diffsync.get(self.diffsync.interface, identifier=attrs["parent"])
            if parent_interface and parent_interface.remote_id:
                nb_params["lag"] = parent_interface.remote_id
            else:
                LOGGER.warning("No Parent interface found for lag member %s %s", self.device_name, self.name)
                nb_params["lag"] = None

        elif "is_lag_member" in attrs and not attrs["is_lag_member"]:
            nb_params["lag"] = None

        return nb_params

    @classmethod
    def create(cls, diffsync: "DiffSync", ids: dict, attrs: dict) -> Optional["DiffSyncModel"]:
        """Create an interface object in Netbox.

        Args:
            diffsync: The master data store for other DiffSyncModel instances that we might need to reference
            ids: Dictionary of unique-identifiers needed to create the new object
            attrs: Dictionary of additional attributes to set on the new object

        Returns:
            NetboxInterface: DiffSync object newly created
        """
        item = super().create(ids=ids, diffsync=diffsync, attrs=attrs)

        try:
            nb_params = item.translate_attrs_for_netbox(attrs)
            intf = diffsync.netbox.dcim.interfaces.create(**nb_params)
            LOGGER.info("Created interface %s (%s) in NetBox", intf.name, intf.id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning(
                "Unable to create interface %s on %s in %s (%s)",
                ids["name"],
                ids["device_name"],
                diffsync.name,
                exc.error,
            )
            return item
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning(
                "Unable to create interface %s on %s in %s (%s)",
                ids["name"],
                ids["device_name"],
                diffsync.name,
                str(exc),
            )
            return item

        item.remote_id = intf.id
        return item

    def update(self, attrs: dict) -> Optional["DiffSyncModel"]:
        """Update an interface object in Netbox.

        Args:
            attrs: Dictionary of attributes to update on the object

        Returns:
            DiffSyncModel: this instance, if all data was successfully updated.
            None: if data updates failed in such a way that child objects of this model should not be modified.

        Raises:
            ObjectNotUpdated: if an error occurred.
        """
        current_attrs = self.get_attrs()

        if attrs == current_attrs:
            return self

        current_attrs.update(attrs)
        nb_params = self.translate_attrs_for_netbox(current_attrs)
        LOGGER.debug("Update interface : %s", nb_params)
        try:
            intf = self.diffsync.netbox.dcim.interfaces.get(self.remote_id)
            intf.update(data=nb_params)
            LOGGER.info("Updated Interface %s %s (%s) in NetBox", self.device_name, self.name, self.remote_id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning(
                "Unable to update interface %s on %s in %s (%s)",
                self.name,
                self.device_name,
                self.diffsync.name,
                exc.error,
            )
            return None

        return super().update(attrs)

    def delete(self) -> Optional["DiffSyncModel"]:
        """Delete an interface object in Netbox.

        Returns:
            NetboxInterface: DiffSync object
        """
        # Check if the interface has some Ips, check if it is the management interface
        if self.ips:
            dev = self.diffsync.get(self.diffsync.device, identifier=self.device_name)
            if dev.primary_ip and dev.primary_ip in self.ips:
                LOGGER.warning(
                    "Unable to delete interface %s on %s, because it's currently the management interface",
                    self.name,
                    dev.name,
                )
                return self
        try:
            intf = self.diffsync.netbox.dcim.interfaces.get(self.remote_id)
            intf.delete()
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning(
                "Unable to delete Interface %s on %s in %s (%s)",
                self.name,
                self.device_name,
                self.diffsync.name,
                exc.error,
            )

        super().delete()
        return self


class NetboxIPAddress(IPAddress):
    """Extension of the IPAddress model."""

    remote_id: Optional[int]

    @classmethod
    def create(cls, diffsync: "DiffSync", ids: dict, attrs: dict) -> Optional["DiffSyncModel"]:
        """Create an IP address in Netbox, if the name of a valid interface is provided the interface will be assigned to the interface.

        Returns:
            NetboxIPAddress: DiffSync object
        """
        interface = None
        if "interface_name" in attrs and "device_name" in attrs:
            interface = diffsync.get(
                diffsync.interface, identifier=dict(device_name=attrs["device_name"], name=attrs["interface_name"])
            )

        try:
            if interface:
                ip_address = diffsync.netbox.ipam.ip_addresses.create(
                    address=ids["address"], interface=interface.remote_id
                )
            else:
                ip_address = diffsync.netbox.ipam.ip_addresses.create(address=ids["address"])
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning("Unable to create the ip address %s in %s (%s)", ids["address"], diffsync.name, exc.error)
            return None

        LOGGER.info("Created IP %s (%s) in NetBox", ip_address.address, ip_address.id)

        item = super().create(ids=ids, diffsync=diffsync, attrs=attrs)
        item.remote_id = ip_address.id

        return item

    def delete(self) -> Optional["DiffSyncModel"]:
        """Delete an IP address in NetBox.

        Returns:
            NetboxIPAddress: DiffSync object
        """
        if self.device_name:
            dev = self.diffsync.get(self.diffsync.device, identifier=self.device_name)
            if dev.primary_ip == self.address:
                LOGGER.warning(
                    "Unable to delete IP Address %s on %s, because it's currently the management IP address",
                    self.address,
                    self.device_name,
                )
                return None

        try:
            ipaddr = self.diffsync.netbox.ipam.ip_addresses.get(self.remote_id)
            ipaddr.delete()
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning(
                "Unable to delete IP Address %s on %s in %s (%s)",
                self.address,
                self.device_name,
                self.diffsync.name,
                exc.error,
            )
            return None

        super().delete()
        return self


class NetboxPrefix(Prefix):
    """Extension of the Prefix model."""

    remote_id: Optional[int]

    def translate_attrs_for_netbox(self, attrs):
        """Translate prefix parameters into Netbox format.

        Args:
            params (dict): Dictionnary of attributes/parameters of the object to translate

        Returns:
            dict: Netbox parameters
        """
        nb_params = {"prefix": self.prefix, "status": "active"}

        site = self.diffsync.get(self.diffsync.site, identifier=self.site_name)
        nb_params["site"] = site.remote_id

        if "vlan" in attrs and attrs["vlan"]:
            vlan = self.diffsync.get(self.diffsync.vlan, identifier=attrs["vlan"])
            if vlan.remote_id:
                nb_params["vlan"] = vlan.remote_id

        return nb_params

    @classmethod
    def create(cls, diffsync: "DiffSync", ids: dict, attrs: dict) -> Optional["DiffSyncModel"]:
        """Create a Prefix in NetBox.

        Returns:
            NetboxPrefix: DiffSync object
        """
        item = super().create(ids=ids, diffsync=diffsync, attrs=attrs)
        nb_params = item.translate_attrs_for_netbox(attrs)

        try:
            prefix = diffsync.netbox.ipam.prefixes.create(**nb_params)
            LOGGER.info("Created Prefix %s (%s) in NetBox", prefix.prefix, prefix.id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning("Unable to create Prefix %s in %s (%s)", ids["prefix"], diffsync.name, exc.error)
            return None

        item.remote_id = prefix.id

        return item

    def update(self, attrs: dict) -> Optional["DiffSyncModel"]:
        """Update a Prefix object in Netbox.

        Args:
            attrs: Dictionary of attributes to update on the object

        Returns:
            DiffSyncModel: this instance, if all data was successfully updated.
            None: if data updates failed in such a way that child objects of this model should not be modified.

        Raises:
            ObjectNotUpdated: if an error occurred.
        """
        current_attrs = self.get_attrs()

        if attrs == current_attrs:
            return self

        nb_params = self.translate_attrs_for_netbox(attrs)

        try:
            prefix = self.diffsync.netbox.ipam.prefixes.get(self.remote_id)
            prefix.update(data=nb_params)
            LOGGER.info("Updated Prefix %s (%s) in NetBox", self.prefix, self.remote_id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning(
                "Unable to update perfix %s in %s (%s)", self.prefix, self.diffsync.name, exc.error,
            )
            return None

        return super().update(attrs)


class NetboxVlan(Vlan):
    """Extension of the Vlan model."""

    remote_id: Optional[int]
    tag_prefix: str = "device="

    def translate_attrs_for_netbox(self, attrs):
        """Translate vlan parameters into Netbox format.

        Args:
            params (dict): Dictionnary of attributes/parameters of the object to translate

        Returns:
            dict: Netbox parameters
        """
        nb_params = {"vid": self.vid}

        if "name" in attrs and attrs["name"]:
            nb_params["name"] = attrs["name"]
        else:
            nb_params["name"] = f"vlan-{self.vid}"

        site = self.diffsync.get(self.diffsync.site, identifier=self.site_name)
        if not site:
            raise ObjectNotFound(f"Unable to find site {self.site_name}")

        nb_params["site"] = site.remote_id

        if "associated_devices" in attrs:
            nb_params["tags"] = [f"device={device}" for device in attrs["associated_devices"]]

        return nb_params

    @classmethod
    def create_from_pynetbox(cls, diffsync: "DiffSync", obj, site_name):
        """Create a new NetboxVlan object from a pynetbox vlan object.

        Args:
            pynetbox ([type]): Vlan object returned by Pynetbox

        Returns:
            NetboxVlan: DiffSync object
        """
        item = cls(vid=obj.vid, site_name=site_name, name=obj.name, remote_id=obj.id)

        # Check the existing tags to learn which device is already associated with this vlan
        # Exclude all vlans that are not part of the inventory
        if obj.tags:
            all_devices = [tag.split(item.tag_prefix)[1] for tag in obj.tags if item.tag_prefix in tag]
            for device in all_devices:
                if diffsync.get(diffsync.device, identifier=device):
                    item.add_device(device)

        return item

    @classmethod
    def create(cls, diffsync: "DiffSync", ids: dict, attrs: dict) -> Optional["DiffSyncModel"]:
        """Create new Vlan in NetBox.

        Returns:
            NetboxVlan: DiffSync object
        """
        try:
            item = super().create(ids=ids, diffsync=diffsync, attrs=attrs)
            nb_params = item.translate_attrs_for_netbox(attrs)
            vlan = diffsync.netbox.ipam.vlans.create(**nb_params)
            item.remote_id = vlan.id
            LOGGER.info("Created Vlan %s in %s (%s)", vlan.get_unique_id(), diffsync.name, vlan.id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning("Unable to create Vlan %s in %s (%s)", ids, diffsync.name, exc.error)
            return None

        return item

    def update_clean_tags(self, nb_params, obj):
        """Update list of vlan tags with additinal tags that already exists on the object in netbox.

        Args:
            nb_params (dict): dict of parameters in netbox format
            obj (pynetbox): Vlan object from pynetbox
        """
        # Before updating the remote vlan we need to check the existing list of tags
        # to ensure that we won't delete an existing tags
        if "tags" in nb_params and nb_params["tags"] and obj.tags:
            for tag in obj.tags:
                if self.tag_prefix not in tag:
                    nb_params["tags"].append(tag)
                else:
                    dev_name = tag.split(self.tag_prefix)[1]
                    if not self.diffsync.get(self.diffsync.device, identifier=dev_name):
                        nb_params["tags"].append(tag)

        return nb_params

    def update(self, attrs: dict) -> Optional["DiffSyncModel"]:
        """Update new Vlan in NetBox.

        Returns:
            NetboxVlan: DiffSync object
        """
        nb_params = self.translate_attrs_for_netbox(attrs)

        try:
            vlan = self.diffsync.netbox.ipam.vlans.get(self.remote_id)
            clean_params = self.update_clean_tags(nb_params=nb_params, obj=vlan)
            vlan.update(data=clean_params)
            LOGGER.info("Updated Vlan %s (%s) in NetBox", self.get_unique_id(), self.remote_id)
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning("Unable to update Vlan %s in %s (%s)", self.get_unique_id(), self.diffsync.name, exc.error)
            return None

        return super().update(attrs)


class NetboxCable(Cable):
    """Extension of the Cable model."""

    remote_id: Optional[int]
    termination_a_id: Optional[int]
    termination_z_id: Optional[int]

    @classmethod
    def create(cls, diffsync: "DiffSync", ids: dict, attrs: dict) -> Optional["DiffSyncModel"]:
        """Create a Cable in NetBox.

        Returns:
            NetboxCable: DiffSync object
        """
        item = super().create(ids=ids, diffsync=diffsync, attrs=attrs)

        interface_a = diffsync.get(
            diffsync.interface, identifier=dict(device_name=ids["device_a_name"], name=ids["interface_a_name"])
        )
        interface_z = diffsync.get(
            diffsync.interface, identifier=dict(device_name=ids["device_z_name"], name=ids["interface_z_name"])
        )

        if not interface_a:
            interface_a = diffsync.get_intf_from_netbox(
                device_name=ids["device_a_name"], intf_name=ids["interface_a_name"]
            )
            if not interface_a:
                LOGGER.info(
                    "Unable to create Cable %s in %s, unable to find the interface %s %s",
                    item.get_unique_id(),
                    diffsync.name,
                    ids["device_a_name"],
                    ids["interface_a_name"],
                )
                return item

        if not interface_z:
            interface_z = diffsync.get_intf_from_netbox(
                device_name=ids["device_z_name"], intf_name=ids["interface_z_name"]
            )
            if not interface_z:
                LOGGER.info(
                    "Unable to create Cable %s in %s, unable to find the interface %s %s",
                    item.get_unique_id(),
                    diffsync.name,
                    ids["device_z_name"],
                    ids["interface_z_name"],
                )
                return item

        if interface_a.connected_endpoint_type:
            LOGGER.info(
                "Unable to create Cable in %s, port %s %s is already connected",
                diffsync.name,
                ids["device_a_name"],
                ids["interface_a_name"],
            )
            return item

        if interface_z.connected_endpoint_type:
            LOGGER.info(
                "Unable to create Cable in %s, port %s %s is already connected",
                diffsync.name,
                ids["device_z_name"],
                ids["interface_z_name"],
            )
            return item

        try:
            cable = diffsync.netbox.dcim.cables.create(
                termination_a_type="dcim.interface",
                termination_b_type="dcim.interface",
                termination_a_id=interface_a.remote_id,
                termination_b_id=interface_z.remote_id,
            )
        except pynetbox.core.query.RequestError as exc:
            LOGGER.warning("Unable to create Cable %s in %s (%s)", ids, diffsync.name, exc.error)
            return item

        interface_a.connected_endpoint_type = "dcim.interface"
        interface_z.connected_endpoint_type = "dcim.interface"

        LOGGER.info("Created Cable %s (%s) in NetBox", item.get_unique_id(), cable.id)
        item.remote_id = cable.id

        return item

    def delete(self):
        """Do not Delete the Cable in NetBox, just print a warning message.

        Returns:
            NetboxCable: DiffSync object
        """
        LOGGER.warning(
            "Cable %s is present in %s but not in the Network, please delete it manually if it shouldn't be in %s",
            self.get_unique_id(),
            self.diffsync.name,
            self.diffsync.name,
        )

        # try:
        #     cable = self.diffsync.netbox.dcim.cables.get(self.remote_id)
        #     cable.delete()
        # except pynetbox.core.query.RequestError as exc:
        #

        super().delete()
        return self