# deburner

Local Ansible playbooks for a disposable Debian CTF laptop. The project currently
provides basic hardening, CTF tooling, and Docker for **Debian 13 (trixie), amd64,
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
`deburner.yml` first runs a read-only Internet preflight, then runs the standard
playbooks in dependency order: hardening, core, network, pivoting, analysis,
steganography/media, Windows/Active Directory and desktop tooling, exploitation tooling,
reverse-engineering tooling, Android tooling, web tooling, SecLists, Docker, C2 tooling, optional
BloodHound staging, user and desktop customizations, and the optional
offline-mirror sync. It then writes a provisioning manifest describing the
resulting installation. Optional features remain disabled unless selected in
`local.yml`. All tasks target
`localhost`; provisioning tasks use privilege escalation. They are idempotent so
you can rerun them during initial provisioning or to apply a deliberate
configuration change, such as opening another port. Rerunning the playbooks is
not a recovery process for a machine that may have been compromised. Review the
repository before running it with root privileges. Keep `local.yml` private; it
is ignored by Git. Each playbook loads `local.yml` automatically, falling back to
`local.yml.example` when it is absent. Nothing commits or pushes changes for you.

The preflight first requires more than 400 GB of free space on the filesystem
containing `/srv`, the planned offline-mirror location. It then requests signed
Debian, Docker and Metasploit repository metadata, GitHub's release API, PyPI and
RubyGems package access, Rust's distribution service and PortSwigger's release
page over certificate-validated HTTPS. It also
checks service-specific response text, which prevents a captive portal's generic
success page from passing. A failure stops `deburner.yml` before any system
changes. The individual category playbooks remain available for deliberate
partial or offline reruns and do not invoke the preflight automatically.

The disk threshold uses decimal gigabytes: 400 GB is 400,000,000,000 bytes. Change
`preflight_storage_path` if the future mirror will live on another filesystem, or
change `preflight_minimum_free_bytes` when deliberately using a different storage
layout. The comparison is strict, so exactly 400 GB free does not pass.

For a preliminary review:

```sh
ansible-playbook deburner.yml --syntax-check
ansible-playbook deburner.yml --check --diff --ask-become-pass
```

Check mode on a fresh machine may fail when a later task needs a package or
directory that was only simulated earlier. It does not verify the live firewall,
kernel, networking, or AppArmor behavior.

### Local validation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) on the
development machine, then create the locked validation environment and run all
local checks:

```sh
make setup
make check
```

`make check` verifies whitespace, checks every YAML file with yamllint, runs
ansible-lint with its production profile, and performs an Ansible syntax check on
every top-level playbook. The development dependencies are isolated in `.venv`
and locked by `uv.lock`; they are not installed globally or provisioned on the
burner. Run `uv lock --upgrade` deliberately when updating the validation tools.
Use `make check` for routine changes. Reserve the resource-intensive `make test`
workflow for substantial provisioning or VM infrastructure changes and final
validation before a release.

### Local test VM lifecycle

The Makefile also provides the host-side lifecycle for a disposable libvirt test
VM. It defines an amd64 KVM guest with four virtual CPUs, 8 GiB RAM, userspace
networking, a serial console and a 450 GiB sparse QCOW2 disk. It uses the current
user's `qemu:///session` connection and stores domain-specific files below
`.test-vm/` in the repository.

On a Debian development host, install the required virtualization commands and
ensure the current user can access `/dev/kvm`:

```sh
sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virtinst passt xorriso
make test-prerequisites
```

Prepare the base image separately when desired:

```sh
make test-image
make test-image-status
```

`test-image` downloads the current official Debian 13 genericcloud amd64 image
from `cloud.debian.org`, reads its expected digest from Debian's `SHA512SUMS`, and
accepts the image only when its SHA-512 digest matches. Debian's `latest`
directory does not currently publish a detached signature for that manifest, so
its authenticity relies on Debian's HTTPS service. The digest still detects
corrupt or incomplete image downloads.

The verified base image is content-addressed and retained below
`.cache/test-vm/`. Every `test-create` checks Debian's current manifest, verifies
the corresponding cached image or downloads it automatically, and creates a new
450 GiB copy-on-write overlay. The overlay consumes space only as the guest
writes data; it depends on the cached base image for its lifetime.

Manage the domain with:

```sh
make test-create
make test-status
make test-start
make test-wait
make test-console   # Leave the console with Ctrl+]
make test-stop
make test-clean
```

`test-stop` requests a graceful shutdown and waits for up to 60 seconds. Use
`make test-destroy` only when a running guest cannot shut down normally.
`test-clean` validates the domain and safety marker, immediately force-stops a
running guest, undefines the domain and removes its overlay and local state. It
refuses an unexpected domain name, state outside this repository, or a directory
without the matching safety marker. It keeps the verified base image for the next test.
`make test-image-purge` removes the complete image cache only when no
`deburner-test` domain exists.

Each new VM receives a NoCloud seed that installs Ansible, a Debian GNOME
baseline and `qemu-guest-agent`, expands the root filesystem, and does not create
credentials for host access. The `passt` userspace network provides outbound
guest access without forwarding any inbound ports. The host communicates with
the guest agent only through the private virtio channel declared in the domain.

`test-wait` reports the elapsed time and sparse overlay usage every 30 seconds
while cloud-init installs the guest agent. Once the channel is available, it
saves `/var/log/cloud-init-output.log` under `.test-results/` and verifies Python
3, root command execution, DNS resolution and root filesystem expansion. The
workflow does not enable, configure, or use SSH.

`test-create` also builds an immutable source ISO from files reported by
`git ls-files --cached --others --exclude-standard`. Ignored files, including
the operator's `local.yml`, Git metadata, caches and earlier test state are not
copied. The guest receives a generated `local.yml` that explicitly disables the
optional offline mirror and BloodHound profile. Cloud-init copies this snapshot
to `/opt/deburner` before the guest agent becomes ready.

After `test-wait`, the remaining stages can be run separately:

```sh
make test-provision
make test-verify
make test-reboot
make test-verify
make test-idempotence
```

`test-provision` runs `deburner.yml` locally as root inside the guest through
the guest agent. Provisioning and the Ansible part of verification stream their
combined output live through QGA and retain the same output under
`.test-results/`. `test-verify` requires the read-only `verify.yml` playbook to
report `changed=0`, then checks the supported platform, GNOME baseline,
hardening, firewall, disabled SSH units, Docker configuration and representative
commands from every tooling category. `test-reboot` requires the guest boot ID
to change and waits for the agent to return. `test-idempotence` reruns the full
playbook, streams its progress and requires Ansible's recap to report
`changed=0`.

The complete workflow is available as one command:

```sh
make test
```

This first runs `make check`, creates a fresh overlay, prepares the GNOME guest,
provisions it, verifies it, reboots and verifies it again, checks idempotence,
then removes the domain and overlay. The verified Debian cloud image remains
cached. Provisioning downloads the complete toolset and can take a long time;
the offline Debian mirror is deliberately excluded.

Run the separate, heavier profile when changing the optional BloodHound role:

```sh
make test-bloodhound
```

It performs the same fresh-VM workflow with `tooling_bloodhound_enabled: true`.
Verification requires the CLI, staging marker, protected initial-credential
file and BloodHound container image, and confirms that the application container
was left stopped. The normal `make test` remains the default profile and does
not download or stage BloodHound.

Ansible and verification logs are retained under `.test-results/`, which is
ignored by Git. Successful tests remove their VM automatically. A failed test
preserves its VM and overlay for inspection; use `make test-status` and
`make test-console`. During development, `make test-refresh` updates
`/opt/deburner` from the same non-ignored working-tree file set through QGA, so a
failed stage can be rerun without rebuilding the guest. Run `make test-clean`
when inspection is complete; it shuts down and removes the disposable VM.

Resource settings and the connection can be overridden for one invocation:

```sh
make test-create TEST_VM_MEMORY_MIB=4096 TEST_VM_VCPUS=2
make test-status TEST_VM_URI=qemu:///session TEST_VM_NAME=deburner-test-small
```

The category playbooks remain directly runnable when you only need one part:

```sh
ansible-playbook preflight.yml
ansible-playbook hardening.yml --ask-become-pass
ansible-playbook tooling-core.yml --ask-become-pass
ansible-playbook tooling-network.yml --ask-become-pass
ansible-playbook tooling-pivoting.yml --ask-become-pass
ansible-playbook tooling-analysis.yml --ask-become-pass
ansible-playbook tooling-media.yml --ask-become-pass
ansible-playbook tooling-windows.yml --ask-become-pass
ansible-playbook tooling-desktop.yml --ask-become-pass
ansible-playbook tooling-exploitation.yml --ask-become-pass
ansible-playbook tooling-reversing.yml --ask-become-pass
ansible-playbook tooling-mobile.yml --ask-become-pass
ansible-playbook tooling-web.yml --ask-become-pass
ansible-playbook tooling-wordlists.yml --ask-become-pass
ansible-playbook docker.yml --ask-become-pass
ansible-playbook tooling-c2.yml --ask-become-pass
ansible-playbook tooling-bloodhound.yml --ask-become-pass \
  --extra-vars tooling_bloodhound_enabled=true
ansible-playbook customization.yml --ask-become-pass
ansible-playbook mirror-sync.yml --ask-become-pass
ansible-playbook mirror-enable.yml --ask-become-pass
ansible-playbook provision-manifest.yml --ask-become-pass
```

## Configuration

Role defaults live in each role's `defaults/main.yml`. Override them in
`local.yml`. For example, to open a host challenge service:

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

### User and desktop customization

`customization.yml` configures the primary desktop account. By default it selects
the account with UID 1000. If the intended user has another UID, set its exact
name in `local.yml`:

```yaml
customization_user: your_username
```

The playbook adds that account to the `sudo` group and grants it passwordless
sudo through `/etc/sudoers.d/deburner-desktop-user`. This gives every process
running as the account a direct path to root privileges, which is intentional for
this disposable CTF workstation. The rule applies only to the selected account.

GNOME uses its dark color preference and the dark Adwaita GTK theme. The role
also configures click-to-focus, a one-hour screen idle delay, US and Finnish
keyboard layouts, two-finger natural touchpad scrolling, a 24-hour clock with
seconds and weekday, battery percentage, disabled hot corners, and calendar week
numbers. Automatic media mounting and opening are disabled.

GNOME automatic suspend is disabled on AC and battery power. A systemd-logind
drop-in ignores lid-close events on battery, external power, and while docked.
Reboot after provisioning to make the logind settings effective; the playbook
does not restart logind underneath the active graphical session. Explicit
shutdown and reboot commands continue to work.

The role installs GNOME Terminal and copies the repository's managed `.vimrc`
and Monokai color scheme into the selected user's home. Bash retains up to one
million in-memory commands and five million commands in its history file.
Ctrl+Backspace deletes the preceding word, grep uses automatic color, and the
managed `.bash_aliases` block provides `copy`, `bat`, and `rot13` aliases.

The customization dependencies include fzf, fd-find (`fdfind`), ripgrep, bat,
wl-clipboard, and the commands used by the configured previews. The managed fzf
configuration enables key bindings, completions, fd-based path generation, and
command-specific previews. Its `fzf-preview.sh` helper is installed in `~/bin`.
Debian's default login profile adds that directory to `PATH` after the next login.

The managed Zed settings enable Vim mode, use the Sublime Text keymap and a dark
Gruvbox theme, disable AI features and telemetry, and configure the requested
panels, language behavior, and compiler integrations. Rust Analyzer comes from
the shared upstream stable Rust toolchain at `/usr/local/bin/rust-analyzer`, so
the settings do not contain a machine-specific home-directory path.

The role also creates `~/.ssh/cm_socket` and a managed block in `~/.ssh/config`
that enables SSH client connection multiplexing and a 60-second server-alive
interval. This does not enable the incoming SSH server, which remains masked by
the hardening role. Existing Bash aliases and SSH configuration outside the
marked blocks are preserved. Git identity is not configured.

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

### Command-and-control tooling

`tooling-c2.yml` installs the current stable checksum-described
[Sliver](https://github.com/BishopFox/sliver/releases) client and server
executables, including Debian's MinGW cross-compilation support. Neither Sliver
executable is started during provisioning.

[Metasploit Framework](https://docs.metasploit.com/docs/using-metasploit/getting-started/nightly-installers.html)
is installed from Rapid7's signed upstream APT repository. The role verifies the
repository key fingerprint and installs the current upstream package, but does
not run `msfdb init` or start `msfconsole`. Initialize its local database
explicitly with `sudo msfdb init` if an exercise needs it.

The role checks out the current [Tuoni](https://docs.tuoni.io/HowToUse/SettingUpTheC2.html)
source under `/srv/tuoni` and, by default, pulls its server, client, documentation,
utility and nginx container images so they remain available after disconnecting.
It does not create Tuoni credentials or certificates, start its containers,
expose ports, or change the firewall. Run `sudo tuoni start` when needed; Tuoni
will then prompt for its local credentials and initialize its configuration. Use
`sudo tuoni print-credentials` to inspect those credentials later and
`sudo tuoni stop` to stop its containers.

Set `tooling_c2_tuoni_stage_images: false` in `local.yml` to install Tuoni's
source without downloading its images during provisioning. The dependency
package list can be replaced with `tooling_c2_packages`. Run `docker.yml` first
when invoking this category playbook directly.

### Core tooling

`tooling-core.yml` installs a general command-line and build environment. Its
Debian packages include Git, curl, wget, jq, ripgrep, tmux, screen, Vim, Nano,
common archive and transfer utilities, Go, Python with pipx and virtual
environments, Flake8, XML tools, apt-file, password-store, and C/C++ build tools
including CMake, Meson and Ninja. It does not install Python applications
globally with pip or change shell and editor configuration.

Rust comes from the official upstream rustup distribution rather than Debian's
`rustc` and `cargo` packages. The role verifies the published rustup-init SHA-256
checksum and installs the latest stable default-profile toolchain under `/opt`.
The role also installs the upstream Rust Analyzer component. The compiler,
Cargo, Clippy, rustfmt, Rust Analyzer and related commands are available through
`/usr/local/bin`. Rerunning the role while online updates the stable toolchain.
Cargo continues to use each user's own writable `~/.cargo` directory for package
downloads and `cargo install` output. The shared toolchain is root-managed, so
use commands such as `sudo rustup target add <target>` when changing it.

The package list is defined in
`roles/tooling_core/defaults/main.yml`. To replace it, copy the complete list to
`tooling_core_packages` in `local.yml`; an empty list is rejected to catch
accidental overrides.

### Network tooling

`tooling-network.yml` installs diagnostics, capture and connectivity tools from
Debian packages: DNS utilities, iproute2, ping, MTR, netcat, Nmap, OpenVPN,
WireGuard tools, OpenSSH and SSHFS clients, proxychains, serial-console tools,
bmon, socat, tcpdump, traceroute, TShark, Wireshark and Whois. Installing these
clients does not enable an inbound network service or open a firewall port.

Unprivileged packet capture is explicitly disabled. Use `sudo tcpdump` or
`sudo tshark` when capture privileges are required, then inspect saved capture
files without root privileges. The package list can be replaced with
`tooling_network_packages` in `local.yml`.

### Pivoting tooling

`tooling-pivoting.yml` installs sshuttle from Debian and the current stable
upstream Chisel and Ligolo-ng releases. Chisel uses its checksum-described amd64
Debian package. Both checksum-described Ligolo-ng amd64 archives are installed,
with the proxy exposed as `ligolo-proxy` and the agent as `ligolo-agent`.

The playbook installs commands only. It does not start a listener or service,
create routes or TUN interfaces, grant Linux capabilities, or open firewall
ports. Invoke the tools deliberately during an exercise; operations that change
interfaces or routes still require suitable privileges. The Debian package list
can be replaced with `tooling_pivoting_packages` in `local.yml`.

### System and file analysis tooling

`tooling-analysis.yml` installs on-demand tools for inspecting processes,
filesystems, disk images, documents, images and local databases. This includes
strace, ltrace, htop, iotop, inotify-tools, Binwalk, ExifTool, YARA, Sleuth Kit,
TestDisk/PhotoRec, SQLite, PostgreSQL and MariaDB clients, and focused
utilities for PDF, PNG, barcode and hexadecimal analysis.

The role installs no audit daemon, continuous collector, scanner service or
scheduled logging task. Every tool runs only when invoked. Some operations, such
as tracing another user's process or reading a raw disk, still require root. The
complete package list can be replaced with `tooling_analysis_packages` in
`local.yml`.

The role also installs the latest stable uv release. Volatility 3 does not publish
an official standalone Linux executable, so the role downloads its checksummed
official release wheel and uses uv to install it in a versioned virtual
environment under `/opt`. `/opt/volatility3` points to the active version, and
the `vol`, `volatility3`, and `volshell` commands use that environment.
Volatility 2 requires Python 2.7, while uv supports Python 3.6 and newer, so it
cannot manage a working Volatility 2 environment. The role instead installs the
final official Volatility 2.6 standalone Linux build under `/opt`, points
`/opt/volatility2` to it, and exposes it as `volatility2`. Volatility 2 is
archived legacy software and receives no updates; keep it only for profiles and
plugins that have not been ported.

### Steganography and media-analysis tooling

`tooling-media.yml` installs Steghide, Stegseek, OutGuess, ImageMagick, FFmpeg,
SoX with its complete format plugins, Audacity, Sonic Visualiser and Foremost
from Debian 13. These provide command-line and graphical workflows for hidden
data, image colour planes, audio spectrograms, metadata, transcoding and file
carving.

The role installs the current checksum-described zsteg gem in an isolated,
versioned directory under `/opt` and exposes it as `zsteg` without changing
Debian's Ruby installation. It also installs the official
[Giotino StegSolve v1.4](https://github.com/Giotino/stegsolve/releases/tag/v1.4)
JAR with a recorded SHA-256 checksum, plus the `stegsolve` command and a GNOME
application launcher. The Debian package list can be replaced with
`tooling_media_packages` in `local.yml`.

### Windows and Active Directory tooling

`tooling-windows.yml` installs SMB and LDAP clients, Hashcat, John the Ripper and
Hydra from Debian. It also installs the current stable Impacket and Certipy
releases and the latest stable [NetExec](https://github.com/Pennyw0rth/NetExec)
release. Each Python toolkit uses a separate, versioned uv environment under
`/opt`, leaving Debian's Python installation unchanged. Every Impacket example
script is exposed with an `impacket-` prefix, including `impacket-secretsdump`,
`impacket-psexec`, `impacket-GetUserSPNs` and `impacket-ntlmrelayx`. Certipy
provides `certipy`; NetExec provides `nxc`, `netexec` and `nxcdb`.

The role selects the platform-independent Impacket and Certipy wheels from their
current PyPI releases and verifies their published SHA-256 digests. NetExec's
official Unix installation uses its Git repository, so the role resolves
GitHub's current non-draft, non-prerelease tag and installs that tagged source.
NetExec currently declares several direct Git dependencies without fixed
commits; uv resolves those dependencies when the burner is provisioned. This
favors current tooling over a fully reproducible dependency set and provides no
independent source signature verification. The role requires the upstream Rust
and uv installations from the earlier standard tooling playbooks.

The same playbook resolves the current commit of
[Responder](https://github.com/lgandx/Responder), checks out that exact commit
under `/opt`, and exposes it as `responder`. Its small Python dependency set comes
from Debian. Responder is not enabled as a service and does not run during
provisioning or at boot. Invoke it deliberately with `sudo`; its poisoning and
rogue-server modes can disrupt a network. A fresh provisioning run tracks the
current upstream commit because Responder does not publish regular stable release
artifacts.

### Optional BloodHound Community Edition

`tooling-bloodhound.yml` installs the latest stable official
[BloodHound CLI](https://github.com/SpecterOps/bloodhound-cli), verifies the
release-provided SHA-256 digest, and uses it to download and initialize the full
BloodHound Community Edition container stack. The umbrella playbook runs this
stage after Docker; when running it separately, apply `docker.yml` first.

BloodHound staging is disabled by default. To include it in the normal one-command
provisioning run, set this in `local.yml` before running `deburner.yml`:

```yaml
tooling_bloodhound_enabled: true
```

The playbook saves the initial CLI output, including the randomly generated local
admin password, in `/root/.config/bloodhound/deburner-initial-install.txt` with
root-only permissions. It then stops and removes the containers while preserving
their images, configuration and named data volumes. This keeps BloodHound off by
default while making it available after the burner loses Internet access.

Start and manage the stack with:

```sh
sudo bloodhound-cli up
sudo cat /root/.config/bloodhound/deburner-initial-install.txt
# Open http://127.0.0.1:8080/ui/login and use the admin account.
sudo bloodhound-cli logs
sudo bloodhound-cli resetpwd  # Generate another password if needed.
sudo bloodhound-cli down
```

The official configuration binds the web interface to localhost. This playbook
does not add a firewall exception or make the UI available to the event network.
BloodHound CE requires at least 8 GB of RAM, four processor cores and roughly
10 GB of storage. Its initial image download and setup can take several minutes.
It is imported by `deburner.yml` after Docker but skipped unless enabled. The
normal VM test leaves it disabled because it is optional and resource intensive.

### Desktop workstation tooling

`tooling-desktop.yml` installs Chromium, GIMP, Meld, PulseAudio Volume Control,
D-Feet and virt-manager from Debian. It also resolves the latest stable Zed
release from the official GitHub repository, verifies the release-provided
SHA-256 digest, and installs it under `/opt` with a stable command and GNOME
launcher. The role does not manage application profiles, settings, extensions or
accounts. Rerunning it while online upgrades Zed when a new stable release is
available.

Debian may install and socket-activate local libvirt components as recommended
dependencies of virt-manager, but this role does not configure a libvirt TCP
listener or open a firewall port. Replace the complete Debian package list with
`tooling_desktop_packages` in `local.yml`; Zed is installed independently of that
list.

### Exploitation and debugging tooling

`tooling-exploitation.yml` installs GDB, multiarch GDB, pwntools, checksec,
patchelf, NASM, QEMU user-mode emulation and glibc debug symbols from Debian 13.
It also installs the current [PEDA](https://github.com/longld/peda) `HEAD` and
loads it for GDB and GDB Multiarch through `/etc/gdb/gdbinit.d/peda.gdb`.

PEDA bundles an old copy of the Python `six` module that does not load on Debian
13. The role makes a one-line compatibility change so PEDA prefers Debian's
`python3-six` package. PEDA itself is otherwise unchanged. It may emit warnings
from its older Python and GDB interfaces.

pwntools comes from Debian as `python3-pwntools`; no global pip environment or
Python account credentials are created. Replace the complete Debian package list
with `tooling_exploitation_packages` in `local.yml` if needed.

### Reverse-engineering tooling

`tooling-reversing.yml` installs the latest stable Ghidra, Binary Ninja Free and
radare2 releases available when the playbook runs. It also builds pycdc and its
`pycdas` bytecode disassembler from the current upstream branch. Ghidra, Binary
Ninja and pycdc use versioned directories under `/opt`, with stable commands for
each tool and GNOME launchers for the graphical applications. radare2 is
installed from its upstream amd64 Debian package because Debian 13 does not
provide it. Ghidra's required OpenJDK 21 and pycdc's build dependencies come from
Debian.

For the three released tools, the role queries each project's official GitHub
latest-release endpoint. It uses the versioned asset URL and SHA-256 digest
returned by that release metadata, so the digest checks the downloaded file
without holding the installation to a repository-pinned version. Because the
metadata and artifact come from the same upstream account, this is a
download-integrity check rather than independent supply-chain verification. The
API queries are unauthenticated so no GitHub token is stored; GitHub can
rate-limit many machines sharing one public address. Downloads require Internet
access and are not supplied by the future Debian mirror.

pycdc does not publish releases or official binary artifacts. The role resolves
the current commit from its official GitHub repository, clones that exact commit,
and builds it with CMake. The commit-addressed installation keeps reruns
idempotent while allowing a fresh provisioning run to use the newest upstream
code. Git transport provides no independent checksum or release-signature check.

Binary Ninja Free requires no account or license key. Its use remains subject to
[Vector 35's Binary Ninja Free license](https://binary.ninja/free/), including
its usage restrictions. Review those terms before running the playbook. No
Binary Ninja credentials are stored by this project.

### Android tooling

`tooling-mobile.yml` always installs ADB, Fastboot, AAPT, APK signing and alignment
utilities, the current stable upstream JADX and Apktool releases, and current
Frida command-line tools and Objection. JADX and Apktool use versioned directories
under `/opt` and stable commands under `/usr/local/bin`. Their GitHub release
artifacts are checked against the SHA-256 digests published in the current
release metadata. Frida and Objection each use a separate uv-managed virtual
environment so their Python dependencies do not modify Debian's Python
installation. Run `tooling-analysis.yml` first when invoking this category
playbook separately, because it provides uv.

The installed Frida commands are host-side clients. The role does not download
or deploy `frida-server`, because its version and architecture must match the
specific target device. It also does not start ADB, attach devices, enable USB
debugging, patch applications or launch instrumentation sessions.

Android Studio and its emulator consume considerably more storage, so their
profile is disabled by default. To install it during the standard provisioning
run, first review the [Android SDK license](https://developer.android.com/studio/terms),
then set both values in `local.yml`:

```yaml
tooling_mobile_android_studio_enabled: true
tooling_mobile_android_licenses_accepted: true
```

The optional profile reads Google's current
[Android Studio download page](https://developer.android.com/studio), selects
the current Linux Android Studio and command-line tool archives, and verifies
their published SHA-256 checksums. It installs the stable SDK platform tools,
emulator, newest stable Google APIs x86_64 system image and matching Android
platform under `/opt/android-sdk`. It creates a stopped, unrooted AVD named
`deburner` for the configured desktop user. That image does not include the Play
Store.

The same profile also installs a distinct Android 14/API 34 Google Play x86_64
image and creates `deburner-rooted`. It resolves the current commit from the
official [rootAVD repository](https://gitlab.com/newbit/rootAVD), checks out that
exact commit under `/opt`, boots the AVD headlessly, and uses rootAVD to patch the
image with the current stable Magisk selected by rootAVD. A separate SDK system
image is required because rootAVD changes the shared `ramdisk.img`; patching the
newest image would also change every AVD that uses it. API 34 is the newest
Android generation explicitly supported by the current rootAVD implementation.
The playbook cold-boots the patched image, verifies the Magisk application and
`su`, then stops the emulator.

rootAVD downloads Magisk using `wget --no-check-certificate`. This is upstream
behavior and means TLS certificate validation does not protect that download.
The role verifies that the resulting ramdisk differs from its backup and boots
with Magisk, but it cannot independently authenticate the downloaded Magisk
artifact. Review rootAVD before enabling this profile. Neither AVD is configured
with a Google account.

The profile can consume tens of gigabytes once Android Studio, the SDK, emulator,
system image and AVD data are present. Group membership takes effect after the
normal post-provisioning reboot. The first interactive `su` request opens a
Magisk prompt inside the rooted AVD; grant `com.android.shell` access there.
Start either AVD from Android Studio's Device Manager or from a terminal:

```sh
emulator -avd deburner
emulator -avd deburner-rooted
adb shell su
```

### Web security tooling

`tooling-web.yml` installs [ffuf](https://github.com/ffuf/ffuf),
[Gobuster](https://github.com/OJ/gobuster),
[sqlmap](https://github.com/sqlmapproject/sqlmap),
[Nikto](https://github.com/sullo/nikto),
[testssl.sh](https://github.com/testssl/testssl.sh),
[jwt_tool](https://github.com/ticarpi/jwt_tool) and
[ysoserial](https://github.com/frohoff/ysoserial) alongside Burp Suite Community
Edition. The first two use the latest
checksum-described official amd64 release archives. testssl.sh and ysoserial use
their latest stable upstream releases. sqlmap, Nikto and jwt_tool use the current
commits from their official repositories; jwt_tool dependencies live in an
isolated uv virtual environment. Stable commands are exposed under
`/usr/local/bin`, while their versioned installations remain under `/opt`.

The playbook detects the latest Professional / Community release listed by
PortSwigger and installs its official standalone JAR under `/opt`. It creates
the `burpsuite` command and a GNOME launcher, using Debian's OpenJDK 21 runtime.
No PortSwigger account, login, paid license or license key is configured. Use is
still subject to
[PortSwigger's terms](https://portswigger.net/burp/eula/community); review them
before running the playbook. If the unified application asks for an edition on
first launch, select Community Edition.

PortSwigger does not provide a working `latest` JAR URL, so the role reads the
current version from its release page and constructs the official versioned
download URL. It deliberately does not pin a checksum. If PortSwigger changes the
release-page format, the role fails instead of guessing a version. The Java heap
limit defaults to 4 GB and can be changed with `tooling_web_burp_max_heap` in
`local.yml`.

The upstream ysoserial release does not provide a checksum for its standalone
JAR, so the role obtains the asset URL from the current non-draft GitHub release
and downloads it over HTTPS without an independent digest. The source-based
installations resolve a concrete current commit before cloning, which keeps a
single provisioning run and its immediate idempotence check consistent while
allowing later fresh installations to receive newer code. Run
`tooling-analysis.yml` before the web playbook when invoking category playbooks
individually, because jwt_tool uses the upstream uv installation.

These upstream installs favor current tools over reproducible builds. Running the
same revision of deburner at different times can install different versions.

### CTF wordlists

`tooling-wordlists.yml` installs the latest stable
[SecLists](https://github.com/danielmiessler/SecLists) release on every standard
provisioning run. Releases use versioned directories under `/opt`; the conventional
`/usr/share/seclists` path points to the active release through `/opt/seclists`.
The role validates that the main discovery, fuzzing, password, and web-shell
directories exist after extraction.

SecLists publishes GitHub releases without checksum-described binary assets. The
role downloads GitHub's generated source archive for the current non-draft,
non-prerelease tag. HTTPS protects the transfer, but there is no independent
release checksum or signature verification. A later fresh provisioning run can
therefore install a newer release.

Ghidra, Binary Ninja Free, Burp and SecLists are large downloads and consume
several gigabytes after extraction. Provision them while the machine has Internet
access.

### Offline Debian mirror

The offline mirror is disabled by default. To include synchronization in the
normal `deburner.yml` run, set this in `local.yml` before provisioning:

```yaml
offline_mirror_enabled: true
```

When enabled, the final provisioning stage starts a large download. Keep the
machine powered on, awake, and connected to the Internet until Ansible reports
that both archive syncs and signature checks have completed. The initial sync can
take several hours depending on the connection and storage speed. The default
12-hour task limit can be changed with `offline_mirror_sync_timeout`; rerunning
the sync resumes from the existing mirror rather than starting from nothing.

You can also leave it disabled during the main run and start it separately:

```sh
ansible-playbook mirror-sync.yml --ask-become-pass \
  --extra-vars offline_mirror_enabled=true
```

The role uses Debian's `debmirror` package to maintain two trees:

- `/srv/deburner/mirror/debian` contains `trixie` and `trixie-updates`.
- `/srv/deburner/mirror/debian-security` contains `trixie-security`.

Both include `amd64` and architecture-independent binary packages from `main`,
`contrib`, `non-free`, and `non-free-firmware`. Source packages, package-content
indexes, Debian Installer images, and CD/DVD images are excluded by default
because they are not needed to install packages on the burner. Set
`offline_mirror_include_sources: true` or
`offline_mirror_include_contents: true` before the initial sync if those files are
needed. The sync checks existing package files and verifies Debian's signed
Release metadata using the archive keyring shipped by Debian.

The 400 GB free-space requirement leaves substantial room for metadata, mirror
growth, containers, and challenge files. Debian's
[`debmirror` size table](https://sources.debian.org/src/debmirror/1%3A2.49/mirror_size)
dated 22 June 2026 lists approximately 123 GB of `trixie` `amd64` and
architecture-independent package payload before security updates and metadata.
Repository size changes over time, so the threshold is a safety margin rather
than a guaranteed final size. Source packages need considerably more space.

Synchronization does **not** change APT's active sources. Run the following only
after the sync has completed and immediately before testing offline operation:

```sh
ansible-playbook mirror-enable.yml --ask-become-pass
```

The activation playbook verifies all three signed `InRelease` files and prepares
two complete source directories under `/etc/deburner/apt`. Existing online source
fragments are preserved in `sources-online`; the local `file:` definitions are
written to `sources-offline`. It then replaces `/etc/apt/sources.list.d` with a
symlink to the selected directory. A legacy `/etc/apt/sources.list`, when present,
is moved into the online directory so it switches modes with the other sources.

After selecting the offline directory, the playbook refreshes APT metadata and
downloads a fresh copy of the `apt` package into a temporary cache. That final
download proves package retrieval works using only the local mirror; it does not
reinstall the package.

After the activation playbook, `/usr/local/sbin/deburner-apt-mode` provides a
direct way to change modes without rerunning Ansible:

```sh
deburner-apt-mode status
sudo deburner-apt-mode offline
sudo deburner-apt-mode online
sudo deburner-apt-mode toggle
```

The small Python utility only changes which Ansible-prepared directory the
`/etc/apt/sources.list.d` symlink points to. It serializes changes with a lock and
runs `apt-get update` after switching. If the update fails, it restores the
previous source directory automatically. The `status` command does not require
root; changing modes does.

Refresh the mirror shortly before disconnecting. Debian security metadata has an
expiry time which APT continues to enforce; this project does not disable
signature or expiry verification. The mirror covers Debian packages only. It
does not contain upstream Docker packages, container images, Git repositories,
Python package indexes, Rust, uv, Volatility, Zed, Ghidra, Binary Ninja, radare2,
Impacket, Certipy, NetExec, Responder, Chisel, Ligolo-ng, Sliver, Metasploit,
Tuoni source or container images, zsteg, StegSolve, BloodHound container images,
ffuf, Gobuster, sqlmap, Nikto, testssl.sh, jwt_tool, ysoserial, Burp Suite or
SecLists. It also does not contain JADX, Apktool, Frida, Objection, Android
Studio, Android SDK components, Android system images, rootAVD or Magisk.

### Provisioning manifest

The final standard provisioning stage writes
`/var/lib/deburner/provision-manifest.json`. It records the Debian release,
architecture and kernel, the repository revision when Git metadata is available,
every installed Debian package and version, immediate entries under `/opt`,
commands under `/usr/local/bin`, installed Rust toolchains, and the IDs, tags and
digests of locally available Docker images.

The manifest deliberately excludes host and user names, environment variables,
configuration contents and credentials. It is an inventory rather than a
software bill of materials or proof that downloaded software is trustworthy.
The generator preserves the file and its timestamp when the collected state has
not changed, which keeps a repeated provisioning run idempotent.

To refresh the inventory after a deliberate installation change, run the
playbook again from the repository checkout:

```sh
ansible-playbook provision-manifest.yml --ask-become-pass
```

Inspect the result with:

```sh
python3 -m json.tool /var/lib/deburner/provision-manifest.json | less
```

## Modifications made

| Area | Modification | Purpose / impact |
| --- | --- | --- |
| Preflight | Require more than 400 GB free for `/srv`; verify Debian, Docker, Metasploit, GitHub, PyPI, RubyGems, Rust and PortSwigger over HTTPS | Stop the umbrella playbook before system changes when storage is insufficient or required Internet services are unavailable or intercepted by a captive portal |
| Packages | Install `apparmor`, `apparmor-utils`, `nftables`, `unattended-upgrades`, `ca-certificates`; apply safe APT upgrades by default | Prepare baseline protections and current packages |
| AppArmor | Enable and start `apparmor.service` | Load installed profiles; applications without profiles remain unconfined |
| SSH | Stop, disable, and mask SSH service/socket when present | Remove unnecessary remote login exposure; SSH clients remain available |
| Updates | Manage `/etc/apt/apt.conf.d/99deburner-updates` | Operator-controlled updates by default; optional security-only unattended upgrades; no automatic reboot |
| Kernel | Manage `/etc/sysctl.d/90-deburner.conf` and apply it | Restrict kernel pointer/dmesg access; protect links, FIFOs and regular files in sticky directories; reject ICMP redirects; disable IPv4 redirect sending; enable SYN cookies |
| Firewall | Manage `/etc/deburner/firewall.nft` and `deburner-firewall.service` | Drop unsolicited host input for IPv4/IPv6; allow loopback, established/related connections, ICMP, DHCP replies, and explicit port exceptions |
| Docker | Configure Docker's signed upstream stable repository; install `docker-ce`, CLI, containerd, Compose and Buildx plugins; manage `/etc/docker/daemon.json`; enable `docker.service` | Provide a current container toolchain with bounded local logs and no network-exposed daemon API |
| Core tooling | Install command-line, archive, Python and native build packages from Debian; install the current upstream stable Rust toolchain with rustup | Provide a general base for CTF tooling without modifying personal shell or editor settings |
| Network tooling | Install diagnostics, VPN clients, scanners and packet-capture tools; keep unprivileged capture disabled | Support event connectivity and network analysis without opening inbound services |
| Pivoting tooling | Install Debian's sshuttle and current checksum-described Chisel and Ligolo-ng releases; leave listeners, routes and interfaces unconfigured | Provide on-demand tunnelling and pivoting clients and servers without changing network state during provisioning |
| C2 tooling | Install current checksum-described Sliver client/server binaries and Rapid7's signed Metasploit package; stage current Tuoni source and container images without initializing or starting the frameworks | Keep Sliver, Metasploit and Tuoni available for authorized exercises while leaving listeners, application containers and local C2 configuration under operator control |
| Analysis tooling | Install on-demand tracing, process inspection, file forensics, metadata, recovery and database client tools; install uv with isolated Volatility 3 and legacy standalone Volatility 2 | Support challenge analysis and live troubleshooting without enabling audit or collection services or modifying the system Python environment |
| Steganography / media tooling | Install Debian media and steganography packages, isolated current zsteg, and the checksum-verified official StegSolve v1.4 JAR | Support hidden-data, image-plane, audio-spectrum, transcoding and file-carving challenges with command-line and graphical tools |
| Windows / AD tooling | Install SMB/LDAP clients, Hashcat, John, Hydra, current stable Impacket, Certipy and NetExec, plus the current Responder source; expose their commands system-wide without enabling Responder | Support Windows and Active Directory discovery, authentication, credential recovery, relay and remote administration exercises without modifying Debian's Python environment or starting listeners |
| BloodHound CE | Optionally install the checksum-described current BloodHound CLI, stage its complete Docker stack, preserve the initial local admin password root-only, and leave the containers stopped | Make graph-based Active Directory analysis available offline without exposing or running its web interface by default |
| Desktop tooling | Install Debian's Chromium, GIMP, Meld, audio control, D-Bus inspection and virtual-machine management applications plus current stable Zed | Provide graphical workstation and editing tools without applying personal preferences |
| Exploitation tooling | Install Debian's GDB, GDB Multiarch, pwntools and related packages; install current PEDA with a Debian 13 compatibility adjustment | Support binary exploitation and debugging without a global pip installation |
| Reverse engineering | Resolve and install current stable Ghidra, Binary Ninja Free and upstream radare2 releases; build the current pycdc and pycdas; add stable commands and desktop launchers | Provide current native, Python-bytecode and Java reverse-engineering tools without accounts or stored license keys |
| Android tooling | Install Android device, APK inspection, signing, decompilation and instrumentation clients; optionally install current Android Studio, SDK, emulator, an unrooted newest-stable AVD, and a rootAVD/Magisk-patched Android 14 AVD | Support static and dynamic Android challenge analysis while keeping the large development and emulation profile configuration-gated |
| Web tooling | Install current ffuf, Gobuster, sqlmap, Nikto, testssl.sh, jwt_tool, ysoserial and Burp Suite Community releases without configuring an account or license | Cover web fuzzing, discovery, injection, TLS, token and Java deserialization work through system-wide commands and an interactive proxy |
| Wordlists | Install the latest stable SecLists release under `/opt` with a conventional `/usr/share/seclists` path | Provide discovery, fuzzing, password, payload and web-shell lists on every burner |
| Customization | Configure the primary user for passwordless sudo, GNOME and power behavior, Vim, Bash history and aliases, fzf integration, Zed settings, and SSH client multiplexing | Keep the disposable workstation awake and ready for event use while applying the requested interactive defaults |
| Offline mirror | Optionally synchronize signed Debian 13 amd64 and architecture-independent packages under `/srv/deburner/mirror`; provide validated activation and `deburner-apt-mode` switching | Permit Debian package installation after disconnecting without exposing a mirror service or silently changing APT during synchronization |
| Provisioning manifest | Record platform details, installed Debian package versions, `/opt` entries, local commands, Rust toolchains, Docker image identities and the available repository revision in `/var/lib/deburner/provision-manifest.json` | Preserve a reviewable inventory of the disposable installation without collecting configuration contents, credentials or user identity |
| Orchestration | Add `deburner.yml` and automatic `local.yml` loading | Run the standard hardening, tooling, Docker, C2, configuration-gated BloodHound and mirror stages, and inventory generation with one command while retaining individual category entry points |
| Verification | Add an optional read-only `verify.yml` playbook | Check the rebooted burner and its provisioning manifest locally without changing it or requiring Internet access |

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

After applying and rebooting, optionally run the read-only verification playbook:

```sh
ansible-playbook verify.yml
```

`verify.yml` is deliberately separate from `deburner.yml`; provisioning does not
run it automatically. It checks the supported platform, services, firewall, SSH
exposure, AppArmor, sysctls, Docker plugins, sudo policy, GNOME preferences,
managed user files, installed commands, optional mirror metadata, and selected
expected entries in the provisioning manifest. It does not change configuration
or require Internet access, so it remains useful after the machine has been
disconnected. It stops with the failed check and prints a
success message only after every check passes. No become-password prompt is
needed because provisioning grants the selected desktop user passwordless sudo;
failure to become root therefore also identifies a broken sudo configuration.

The following commands provide additional manual inspection:

```sh
sudo systemctl status deburner-firewall apparmor
sudo nft list table inet deburner
sudo aa-status
sudo sysctl kernel.kptr_restrict kernel.dmesg_restrict fs.protected_regular
sudo apt-config dump
sudo docker info
sudo docker compose version
sudo docker buildx version
python3 -m json.tool /var/lib/deburner/provision-manifest.json | less
command -v git rg python3 pipx go rustup rustc rust-analyzer cargo clippy-driver rustfmt flake8 apt-file nmap openvpn wg tcpdump tshark wireshark
command -v chisel sshuttle ligolo-agent ligolo-proxy
command -v sliver-client sliver-server msfconsole msfvenom tuoni
rustup --version
rustc --version
cargo --version
command -v strace ltrace htop binwalk exiftool sqlite3 yara fls photorec
command -v steghide stegseek zsteg outguess magick ffmpeg sox audacity sonic-visualiser foremost stegsolve
command -v uv uvx vol volatility2 volatility3 volshell
command -v smbclient ldapsearch hashcat john hydra responder certipy
command -v impacket-GetUserSPNs impacket-ntlmrelayx impacket-psexec impacket-secretsdump impacket-wmiexec nxc netexec nxcdb
command -v chromium gimp meld pavucontrol d-feet virt-manager zed
command -v gdb gdb-multiarch pwn checksec r2 radare2 pycdc pycdas ghidra binaryninja burpsuite
command -v adb fastboot aapt apksigner zipalign apktool jadx jadx-gui frida frida-ps objection
command -v ffuf gobuster sqlmap nikto testssl.sh jwt_tool ysoserial
gdb --batch -ex 'peda show option' -ex quit
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

- **Optional screen sharing:** VNC sharing of the existing GNOME Wayland desktop
  remains deferred. WayVNC supports wlroots-based compositors and explicitly does
  not support GNOME, so it cannot meet the current desktop requirement. See the
  [WayVNC compatibility note](https://github.com/any1/wayvnc#introduction).

Screen sharing remains a recorded requirement, not an implemented playbook.
