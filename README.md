# deburner

Local Ansible playbooks for a disposable Debian CTF laptop. The project currently
provides basic hardening and Docker tooling for **Debian 13 (trixie), amd64,
systemd**, with a GNOME/Wayland desktop. Debian 14 will require an explicit review
and version bump; the playbooks refuse unsupported releases rather than following
`stable` silently.

> [!WARNING]
> This project is heavily AI-assisted and may contain mistakes, unsafe assumptions,
> incomplete protections, or changes that do not suit your hardware, network, or
> threat model. It is provided as-is, without warranty, and the project author is
> not responsible for damage, data loss, security incidents, or other consequences
> of its use. Review and understand every playbook and generated configuration
> before granting it root privileges. Test it in an environment you control and
> make your own informed decisions about which changes to apply.

This baseline prioritizes CTF compatibility. It does not contain malware or
isolate the laptop from other devices: outbound traffic is unrestricted. Use the
event network or an isolated lab network. The intended lifecycle is a fresh
Debian installation, one provisioning phase, use during one event, and a secure
erase of the entire disk afterward. Never treat an event-exposed installation as
trusted again. Choose disk encryption during Debian's installation; this project
does not repartition disks, configure encryption, or erase the disk.

## Run on a fresh Debian installation

Install Debian 13 amd64 with GNOME and a user allowed to use `sudo`. Prepare the
machine while it still has Internet access. If your user cannot run `sudo`, use
`su -` to install `sudo` and add the user to its group, then log in again.

```sh
sudo apt update
sudo apt install sudo git ansible
git clone https://github.com/sanduuz/deburner.git
cd deburner
cp local.yml.example local.yml
# Edit local.yml for this event, then:
ansible-playbook deburner.yml --ask-become-pass
sudo reboot
```

The canonical repository is
[github.com/sanduuz/deburner](https://github.com/sanduuz/deburner). No third-party
Ansible collections are required.

Run Ansible as your normal user; `--ask-become-pass` supplies the sudo password.
`deburner.yml` runs the standard playbooks in dependency order: hardening first,
then Docker. Future optional playbooks that expose services, such as screen
sharing, will not be included. All tasks target `localhost` and use privilege
escalation. They are idempotent so you can rerun them during initial provisioning
or to apply a deliberate configuration change, such as opening another port.
Rerunning the playbooks is not a recovery process for a machine that may have
been compromised. Review the repository before running it with root privileges.
Keep `local.yml` private; it is ignored by Git. Each playbook loads `local.yml`
automatically, falling back to `local.yml.example` when it is absent. Nothing
commits or pushes changes for you.

For a preliminary review:

```sh
ansible-playbook deburner.yml --syntax-check
ansible-playbook deburner.yml --check --diff --ask-become-pass
```

Check mode on a fresh machine may fail when a later task needs a package or
directory that was only simulated earlier. It does not verify the live firewall,
kernel, networking, or AppArmor behavior.

The category playbooks remain directly runnable when you only need one part:

```sh
ansible-playbook hardening.yml --ask-become-pass
ansible-playbook docker.yml --ask-become-pass
```

## Configuration

Defaults live in `roles/hardening/defaults/main.yml`. Override them in `local.yml`.
For example, to open a host challenge service:

```yaml
hardening_allowed_tcp_ports: [8080]
hardening_allowed_udp_ports: []
```

These ports are allowed from **any IPv4 or IPv6 source**, on every interface.
Change the list and rerun the playbook if the event configuration changes.
Existing tracked connections can remain open until disconnected. Screen sharing
is not installed or opened by this baseline.

`hardening_upgrade_packages: true` applies available updates during preparation.
Set it to `false` when you need to avoid package upgrades during an event. The role
still installs required packages and refreshes APT metadata when the cache is old;
it is not yet an offline provisioning playbook.

`hardening_automatic_security_updates: false` keeps background update checks and
unattended upgrades disabled for predictable exercises and offline use. Set it to
`true` to enable automatic Debian security updates. Automatic reboots remain
disabled. Otherwise update manually before an event:

```sh
sudo apt update
sudo apt upgrade
```

The role preserves your existing APT repositories. Ensure the Debian security
repository is enabled; see [Debian's sources documentation](https://wiki.debian.org/SourcesList).

`hardening_disable_ssh: true` stops, disables, and masks installed `ssh.service`
and `ssh.socket`. Setting it to `false` skips those tasks; it does not undo an
earlier mask. To deliberately restore SSH, unmask its units, start the desired
service, and explicitly allow its port in `local.yml`.

You can override individual values in `hardening_sysctl` by defining the **entire
mapping** in `local.yml`; Ansible replaces dictionaries by default. Removing a
previously managed key does not restore its live value automatically. Restore it
explicitly or reboot after removing it from the managed configuration.

### Docker

The Docker playbook configures Docker's official `stable` APT repository and
installs its latest Docker Engine, CLI, containerd, Compose and Buildx packages.
The repository is limited to Debian 13 (`trixie`) on `amd64` and is authenticated
with a dedicated key under `/etc/apt/keyrings`; the playbook verifies the key's
full fingerprint before APT uses it. It removes Debian's conflicting Docker,
containerd and runc packages first, as required by
[Docker's Debian installation guide](https://docs.docker.com/engine/install/debian/).
Existing data under `/var/lib/docker` is not deleted during that package change.

Versions are not pinned, so a later provisioning rerun can install a newer stable
release. The offline Debian mirror will not contain these upstream Docker
packages. Install Docker and pull any required images while online; preserving
Docker packages and images for fully offline reinstallation is separate future
work.

By default, Docker commands require `sudo`. To use the Docker socket as your normal
user, add the exact local account name to `local.yml` before running the playbook:

```yaml
docker_group_users: [your_username]
```

Log out and back in after the playbook changes group membership. Access to the
Docker socket is root-equivalent: a user or process with that access can mount the
host filesystem, start privileged containers, and take complete control of the
machine. Only add trusted interactive users. The daemon listens on its local Unix
socket; this playbook does not expose its API over TCP.

The playbook owns `/etc/docker/daemon.json`. Its defaults use Docker's rotating
`local` log driver with a 20 MB limit and five retained files per container, and
enable live restore. Override the related `docker_*` variables only after checking
that the installed daemon supports the chosen values.

## Modifications made

| Area | Modification | Purpose / impact |
| --- | --- | --- |
| Packages | Install `apparmor`, `apparmor-utils`, `nftables`, `unattended-upgrades`, `ca-certificates`; apply safe APT upgrades by default | Prepare baseline protections and current packages |
| AppArmor | Enable and start `apparmor.service` | Load installed profiles; applications without profiles remain unconfined |
| SSH | Stop, disable, and mask SSH service/socket when present | Remove unnecessary remote login exposure; SSH clients remain available |
| Updates | Manage `/etc/apt/apt.conf.d/99deburner-updates` | Operator-controlled updates by default; optional security-only unattended upgrades; no automatic reboot |
| Kernel | Manage `/etc/sysctl.d/90-deburner.conf` and apply it | Restrict kernel pointer/dmesg access; protect links, FIFOs and regular files in sticky directories; reject ICMP redirects; disable IPv4 redirect sending; enable SYN cookies |
| Firewall | Manage `/etc/deburner/firewall.nft` and `deburner-firewall.service` | Drop unsolicited host input for IPv4/IPv6; allow loopback, established/related connections, ICMP, DHCP replies, and explicit port exceptions |
| Docker | Configure Docker's signed upstream stable repository; install `docker-ce`, CLI, containerd, Compose and Buildx plugins; manage `/etc/docker/daemon.json`; enable `docker.service` | Provide a current container toolchain with bounded local logs and no network-exposed daemon API |
| Orchestration | Add `deburner.yml` and automatic `local.yml` loading | Run the standard hardening and Docker playbooks with one command while retaining individual category entry points |

The firewall replaces only the `inet deburner` table in one nftables transaction.
It has no output or forwarding chain, does not flush the global ruleset, and
validates proposed rules with `nft --check` before installing them. The separate
service loads the firewall at boot and supports reloads without disturbing
Docker's rules. The playbook refuses an active or enabled `nftables.service`
because its default global flush conflicts with this approach. Other firewall
managers such as UFW/firewalld are outside this fresh-install baseline; resolve
competing policies before using it.

Docker-published ports use forwarding and are **not protected by this host input
policy**. Bind private containers to localhost (for example,
`-p 127.0.0.1:8080:80`) or publish them only on the event network deliberately.
See [Docker's firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).
The Docker playbook leaves Docker's firewall management enabled because disabling
it usually breaks container networking. It does not create or publish containers.

The baseline retains Debian's ptrace, perf, user namespace, core dump, IPv6 and
IP-forwarding defaults. It does not add USB restrictions or compiler/tool bans.
Kernel log restrictions require `sudo dmesg` when diagnosing drivers. The
filesystem protections can change the behavior of challenges relying on unsafe
file access in shared sticky directories.

## Verify the provisioned machine

After applying and rebooting:

```sh
sudo systemctl status deburner-firewall apparmor
sudo nft list table inet deburner
sudo aa-status
sudo sysctl kernel.kptr_restrict kernel.dmesg_restrict fs.protected_regular
sudo apt-config dump
sudo docker info
sudo docker compose version
sudo docker buildx version
```

Verify Wi-Fi, DHCP, DNS and any event VPN from the laptop. From a second machine
on the event network, confirm a listening host test service is inaccessible until
its port is explicitly allowed, then confirm it becomes reachable after a rerun.
Later, test Docker separately: outbound container access and intentionally
published ports must keep working after a firewall reload. A simple online engine
test is `sudo docker run --rm hello-world`; it downloads an image from Docker Hub.
If Docker group access was enabled, repeat `docker info` without `sudo` only after
logging out and back in.

If verification fails during initial provisioning, inspect the Ansible error and
the relevant system logs, correct the configuration, and rerun the playbook. Once
the machine has joined an untrusted event network or handled challenge material,
do not use these playbooks to return it to a trusted state.

## End of event

Securely erase the entire disk, including the operating system, swap, local
mirror, containers, challenge data, credentials and logs. The exact erase method
depends on the drive technology and system firmware; use the drive vendor's
documented sanitize or secure-erase operation and verify that it completed. A
normal file deletion, filesystem format, Ansible rerun, or Debian reinstall is
not the end-of-event sanitization procedure.

## Next categories

- **Tooling:** selected CTF, debugging, reverse engineering, and network tools in
  their own playbook. Tool selection remains to be agreed.
- **Optional screen sharing:** share the existing GNOME Wayland desktop with
  teammates, preferably view-only, with an explicit enable/disable workflow and
  event-network restrictions. GNOME's supported desktop-sharing workflow uses
  RDP; a VNC-specific solution needs separate investigation. See
  [GNOME desktop sharing](https://help.gnome.org/gnome-help/sharing-desktop.html).
- **Customizations / offline mirror:** implement a complete Debian 13 **amd64
  package mirror**, including architecture-independent packages, all four
  components (`main`, `contrib`, `non-free`, `non-free-firmware`), `trixie`,
  `trixie-updates`, and `trixie-security`, under `/srv/deburner/mirror`. Source
  packages can be included as an option; installing binary packages offline does
  not require them. Use signed upstream metadata and local `file:` APT sources;
  no inbound mirror server is needed. Synchronization and switching APT to offline
  mode must be separate, explicit steps, with signature/hash verification and a
  successful offline installation test before disconnecting. Account for security
  metadata expiry without disabling signature verification. Do not assume a
  512 GB system disk has enough free space: check current mirror size and allow
  room for the OS, containers and challenges before syncing. This will cover
  Debian packages, not PyPI, Git repositories, Docker images or other ecosystems.
  [Debmirror](https://manpages.debian.org/trixie/debmirror/debmirror.1.en.html)
  supports architecture, suite and component selection.

These categories are recorded requirements, not implemented playbooks yet.
