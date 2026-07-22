# Copyright 2020 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import collections
import configparser
import copy
import subprocess
from urllib.parse import urlparse

import charmhelpers.core.hookenv as hookenv
import charmhelpers.contrib.openstack.utils as os_utils
import charms_openstack.charm
import charms_openstack.adapters
import charms_openstack.plugins
import charms_openstack.ip as os_ip

import charms.reactive as reactive

charms_openstack.plugins.trilio.make_trilio_handlers()


def _get_internal_url(identity_service, service):
    ep_catalog = identity_service.relation.endpoint_checksums()
    if service in ep_catalog:
        return ep_catalog.get(service)["internal"]
    return None



@charms_openstack.adapters.adapter_property("identity-service")
def neutron_url(identity_service):
    return _get_internal_url(identity_service, "neutron")


@charms_openstack.adapters.adapter_property("identity-service")
def cinder_url(identity_service):
    return _get_internal_url(identity_service, "cinderv2")


@charms_openstack.adapters.adapter_property("identity-service")
def glance_url(identity_service):
    return _get_internal_url(identity_service, "glance")


@charms_openstack.adapters.adapter_property("identity-service")
def nova_url(identity_service):
    return _get_internal_url(identity_service, "nova")


class IdentityServiceIncompleteException(Exception):
    """Signal that the identity-service relation is not complete"""

    pass


class LicenseFileMissingException(Exception):
    """Signal that the license file has not been provided as a resource"""

    pass


class TrilioWLMDatabaseAdapter(
        charms_openstack.adapters.DatabaseRelationAdapter):
    """
    Overrides default class to force use of the mysqldb driver
    """

    @property
    def driver(self):
        return "mysql"


class TrilioWLMCharmRelationAdapters(
        charms_openstack.adapters.OpenStackAPIRelationAdapters):
    """
    Adapters collection to append specific adapters for TrilioWLM
    """
    relation_adapters = {
        'amqp': charms_openstack.adapters.RabbitMQRelationAdapter,
        'shared_db': TrilioWLMDatabaseAdapter,
        'cluster': charms_openstack.adapters.PeerHARelationAdapter,
        'coordinator_memcached': (
            charms_openstack.adapters.MemcacheRelationAdapter
        ),
    }


class TrilioWLMBaseCharm(charms_openstack.plugins.TrilioVaultCharm):

    abstract_class = True
    # Internal name of charm
    service_name = name = "workloadmgr"

    adapters_class = TrilioWLMCharmRelationAdapters

    workloadmgr_conf = "/etc/triliovault-wlm/triliovault-wlm.conf"
    api_paste_ini = "/etc/triliovault-wlm/api-paste.ini"
    alembic_ini = "/etc/triliovault-wlm/alembic.ini"
    workloadmgr_log_conf = "/etc/triliovault-wlm/wlm_logging.conf"

    base_packages = [
        "linux-image-virtual",  # Used for libguestfs supermin appliance
        "nova-common",
        "workloadmgr",
        "python3-workloadmgrclient",
        "python3-contegoclient",
        "python3-s3-fuse-plugin",
        "python-apt",
        "python3-trilio-dms",
    ]

    python_version = 3

    # Ensure we use the right package for versioning
    version_package = "workloadmgr"

    api_ports = {
        "workloadmgr-api": {
            os_ip.PUBLIC: 8780,
            os_ip.ADMIN: 8780,
            os_ip.INTERNAL: 8780,
        }
    }

    service_type = "workloadmgr"
    default_service = "workloadmgr-api"

    required_relations = ["shared-db", "amqp", "identity-service"]

    ha_resources = ["vips", "haproxy"]

    release_pkg = "workloadmgr"

    package_codenames = {
        "workloadmgr": collections.OrderedDict(
            [("3", "stein"), ("4", "train")]
        ),
        "nova-common": os_utils.PACKAGE_CODENAMES["nova-common"],
    }

    sync_cmd = [
        "alembic",
        "--config={}".format(alembic_ini),
        "upgrade",
        "head",
    ]

    user = "root"
    group = "nova"

    required_services = [
        "nova",
        "neutron",
        "glance",
        "cinderv2",
        "cinderv3",
        "cinder",
    ]

    os_release_pkg = 'nova-common'

    workloadmgr_install_dir = "/usr/lib/python3/dist-packages/workloadmgr"

    endpoint_template = "{}/v1/$(tenant_id)s"

    def __init__(self, release=None, **kwargs):
        super().__init__(release="stein", **kwargs)


    @property
    def is_vmware_migration_enabled(self):
        return hookenv.config("is-vmware-migration-enabled")

    # List of packages to install for this charm
    # NOTE(jamespage): nova-common ensures a consistent UID is use
    # for the nova user.

    def get_amqp_credentials(self):
        return ("triliowlm", "triliowlm")

    def get_database_setup(self):
        return [{"database": self.service_type, "username": self.service_type}]

    def _alembic_db_uri(self):
        """Return the sqlalchemy.url configured in alembic.ini"""
        parser = configparser.ConfigParser()
        parser.read(self.alembic_ini)
        return parser.get("alembic", "sqlalchemy.url")

    def _db_schema_is_complete(self):
        """Verify a column added by the job-table migration actually exists.

        Works around a race between db_sync and the co-located
        mysql-router subordinate restarting its router process while the
        shared-db relation is still settling: alembic can report a clean
        run (alembic_version at head) while some migrations' DDL was
        silently not applied, leaving e.g. the job table without its
        created_at/updated_at/etc columns (TVAULT-7502).
        """
        uri = urlparse(self._alembic_db_uri())
        try:
            output = subprocess.check_output(
                [
                    "mysql",
                    "--host={}".format(uri.hostname),
                    "--port={}".format(uri.port or 3306),
                    "--user={}".format(uri.username),
                    "--password={}".format(uri.password),
                    "--batch",
                    "--skip-column-names",
                    uri.path.lstrip("/"),
                    "-e",
                    "SHOW COLUMNS FROM job LIKE 'created_at'",
                ],
                universal_newlines=True,
            )
        except subprocess.CalledProcessError as e:
            hookenv.log(
                "db_sync schema verification query failed: {}".format(e),
                level=hookenv.WARNING,
            )
            return False
        return bool(output.strip())

    # Revision immediately before migration 016 creates the job table.
    # Migrations below this point (e.g. 006_snap_network_resources_cascading)
    # deliberately raise NotImplementedError() from their downgrade(), so a
    # full "downgrade base" aborts partway through and leaves the schema in
    # a worse state -- confirmed while validating this fix. 015 is also the
    # exact revision QA's own manual recovery downgraded to successfully.
    _DB_REPAIR_BASE_REVISION = "015"

    def db_sync(self):
        """Perform a database sync using the command defined in the
        self.sync_cmd attribute, then verify the resulting schema.

        Overrides charms_openstack.charm.OpenStackCharm.db_sync() to add
        post-sync verification. alembic can exit 0 and record
        alembic_version at head while some migrations' DDL was not
        actually applied -- observed when db_sync races with the
        co-located mysql-router subordinate restarting its router process
        during initial sync (TVAULT-7502). When that happens, this repairs
        the schema by forcing alembic to walk the migration chain again
        from just before the job table is created (downgrade to
        _DB_REPAIR_BASE_REVISION, then upgrade head). This is only safe
        because db_sync (gated by the db-sync-done leader setting) runs at
        most once, on the very first sync of a brand new deployment,
        before any real data can exist in these tables.
        """
        if not self.db_sync_done() and hookenv.is_leader():
            subprocess.check_call(self.sync_cmd)
            if not self._db_schema_is_complete():
                hookenv.log(
                    "db_sync produced an incomplete schema (job.created_at "
                    "missing despite alembic_version at head); forcing a "
                    "downgrade/upgrade cycle to repair it. See TVAULT-7502.",
                    level=hookenv.WARNING,
                )
                subprocess.check_call(
                    self.sync_cmd[:2] +
                    ["downgrade", self._DB_REPAIR_BASE_REVISION])
                subprocess.check_call(self.sync_cmd)
                if not self._db_schema_is_complete():
                    raise Exception(
                        "workloadmgr database schema is still incomplete "
                        "after a repair attempt; manual intervention is "
                        "required. See TVAULT-7502."
                    )
            hookenv.leader_set({"db-sync-done": True})
            self.restart_all()

    @property
    def public_url(self):
        """Return the public endpoint URL for the default service as specified
        in the self.default_service attribute
        """
        return self.endpoint_template.format(super().public_url)

    @property
    def admin_url(self):
        """Return the admin endpoint URL for the default service as specificed
        in the self.default_service attribute
        """
        return self.endpoint_template.format(super().admin_url)

    @property
    def internal_url(self):
        """Return the internal internal endpoint URL for the default service as
        specificated in the self.default_service attribtue
        """
        return self.endpoint_template.format(super().internal_url)


    dms_server_conf = "/etc/triliovault-dms/server.conf"
    dms_client_conf = "/etc/triliovault-dms/client.conf"
    dms_s3vaultfuse_conf = "/etc/triliovault-dms/s3vaultfuse-global.conf"

    @property
    def services(self):
        """Determine the services associated with this class"""
        _svcs = ["wlm-api", "wlm-scheduler", "wlm-workloads", "trilio-dms-server"]

        if not reactive.flags.is_flag_set("ha.available"):
            # Only manage wlm-cron service when running solo as an
            # instance across the cluster which will be managed by
            # corosync and pacemaker
            _svcs.append("wlm-cron")

        return _svcs

    @property
    def restart_map(self):
        """Generate the restart map for this service"""
        _restart_map = {
            self.api_paste_ini: ["wlm-api"],
            self.alembic_ini: [],
            self.workloadmgr_log_conf: self.services,
            self.dms_server_conf: ["trilio-dms-server"],
            self.dms_client_conf: ["trilio-dms-server"],
            self.dms_s3vaultfuse_conf: ["trilio-dms-server"],
        }

        return _restart_map

    def configure_ha_resources(self, hacluster):
        """Inform the ha subordinate about each service it should manage.

        Delegate core resources to the parent class and add wlm-cron as
        and additional init service to manage

        @param hacluster instance of interface class HAClusterRequires
        """
        super().configure_ha_resources(hacluster)
        hacluster.add_systemd_service(self.name, "wlm-cron", clone=False)

    def create_trust(self, identity_service, cloud_admin_password):
        """Create trust between Trilio WLM service user and Cloud Admin
        """
        if not hookenv.is_leader():
            raise charms_openstack.plugins.classes.UnitNotLeaderException(
                "please run on leader unit")
        if not identity_service.base_data_complete():
            raise IdentityServiceIncompleteException(
                "identity-service relation incomplete"
            )
        # NOTE(jamespage): hardcode of admin username here may be brittle
        subprocess.check_call(
            [
                "workloadmgr",
                "--os-username",
                "admin",
                "--os-password",
                cloud_admin_password,
                "--os-auth-url",
                "{}://{}:{}/v3".format(
                    identity_service.service_protocol(),
                    identity_service.service_host(),
                    identity_service.service_port(),
                ),
                "--os-user-domain-name",
                "admin_domain",
                "--os-project-domain-id",
                identity_service.admin_domain_id(),
                "--os-project-id",
                identity_service.admin_project_id(),
                "--os-project-name",
                "admin",
                "--os-region-name",
                hookenv.config("region"),
                "trust-create",
                "--is_cloud_trust",
                "True",
                "Admin",
            ]
        )
        hookenv.leader_set({"trusted": True})

    def create_license(self, identity_service):
        if not hookenv.is_leader():
            raise charms_openstack.plugins.classes.UnitNotLeaderException(
                "please run on leader unit")
        license_file = hookenv.resource_get("license")
        if not license_file:
            raise LicenseFileMissingException(
                "License file not provided as a resource"
            )
        if not identity_service.base_data_complete():
            raise IdentityServiceIncompleteException(
                "identity-service relation incomplete"
            )
        subprocess.check_call(
            [
                "workloadmgr",
                "--os-username",
                identity_service.service_username(),
                "--os-password",
                identity_service.service_password(),
                "--os-auth-url",
                "{}://{}:{}/v3".format(
                    identity_service.service_protocol(),
                    identity_service.service_host(),
                    identity_service.service_port(),
                ),
                "--os-user-domain-name",
                "service_domain",
                "--os-project-domain-id",
                identity_service.service_domain_id(),
                "--os-project-id",
                identity_service.service_tenant_id(),
                "--os-project-name",
                identity_service.service_tenant(),
                "--os-region-name",
                hookenv.config("region"),
                "license-create",
                license_file,
            ]
        )
        hookenv.leader_set({"licensed": True})

    @property
    def licensed(self):
        return hookenv.leader_get("licensed")

    @property
    def trusted(self):
        return hookenv.leader_get("trusted")

    def custom_assess_status_check(self):
        return None, None


    def custom_assess_status_last_check(self):
        """Check required configuration options are set"""
        if not self.trusted:
            return (
                "blocked",
                "application not trusted; please run "
                "'create-cloud-admin-trust' action",
            )
        if not self.licensed:
            return (
                "blocked",
                "application not licensed; please run 'create-license' action",
            )
        return None, None

    @classmethod
    def trilio_version_package(cls):
        return 'workloadmgr'


class TrilioWLMCharmStein41(
    TrilioWLMBaseCharm, charms_openstack.plugins.TrilioVaultCharmGhostAction
):
    release = "stein"
    trilio_release = "4.1"
    python_version = 2


class TrilioWLMCharmStein42(
    TrilioWLMBaseCharm, charms_openstack.plugins.TrilioVault42CharmGhostAction
):
    release = "stein"
    trilio_release = "4.2"


class TrilioWLMCharmUssuri41Base(TrilioWLMBaseCharm):

    abstract_class = True

    base_packages = [
        "linux-image-virtual",  # Used for libguestfs supermin appliance
        "nova-common",
        "workloadmgr",
        "python3-workloadmgrclient",
        "python3-contegoclient",
        "python3-s3-fuse-plugin",
        "python3-glanceclient",
        "python3-neutronclient",
        "python3-apt",
        "python3-retrying",
    ]

    all_packages = [
        "linux-image-virtual",  # Used for libguestfs supermin appliance
        "nova-common",
        "workloadmgr",
        "python3-workloadmgrclient",
        "python3-contegoclient",
        "python3-s3-fuse-plugin",
        "python3-glanceclient",
        "python3-neutronclient",
        "python3-apt",
        "python3-retrying",
    ]



class TrilioWLMCharmUssuri41(
    TrilioWLMCharmUssuri41Base,
    charms_openstack.plugins.TrilioVaultCharmGhostAction
):
    # First release supported
    release = "ussuri"
    trilio_release = "4.1"


class TrilioWLMCharmUssuri42(
    TrilioWLMCharmUssuri41Base,
    charms_openstack.plugins.TrilioVault42CharmGhostAction
):
    # First release supported
    release = "ussuri"
    trilio_release = "4.2"
