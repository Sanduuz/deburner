# Pre-event provisioning checklist

Use this checklist for a newly installed disposable Debian machine before each
event. It complements the detailed [README](README.md); it does not replace
reviewing the playbooks and deciding whether their configuration is appropriate
for the event.

The intended lifecycle is: install Debian, provision once while online, use the
machine for the event, and securely erase the entire disk afterward. Rerunning a
playbook during initial preparation can apply a deliberate configuration change.
It is not a way to restore trust after the machine has handled untrusted event
traffic or files.

## 1. Prepare the machine

- [ ] Back up anything that must be kept. Treat every file on this disk as
      disposable.
- [ ] Install Debian 13 amd64 with GNOME and disk encryption.
- [ ] Set the short host name to `deburner`. Confirm it with `hostname -s`.
- [ ] Confirm that the account used for provisioning can run `sudo`.
- [ ] Confirm that `/srv` is on the intended disk and has more than 400 GB free
      with `df -h /srv`. A disk of at least 512 GB is recommended.
- [ ] Connect to a trusted provisioning network with unrestricted HTTPS access
      and no captive portal.
- [ ] Connect external power and allow enough time for large tool downloads.
      An offline mirror can take several hours.
- [ ] Enable hardware virtualization in the firmware if the optional Android
      Studio profile and its emulators will be used.

## 2. Fetch and review deburner

- [ ] Install the bootstrap packages and clone the canonical repository:

  ```sh
  sudo apt update
  sudo apt install sudo git ansible make
  git clone https://github.com/sanduuz/deburner.git
  cd deburner
  cp local.yml.example local.yml
  ```

- [ ] Review the checked-out revision, the README, `deburner.yml`, `local.yml`,
      and the playbooks that will run. The project is heavily AI-assisted and is
      provided without warranty; make your own decision about every root-level
      change.
- [ ] Keep `local.yml` private. It is ignored by Git.
- [ ] Validate the effective configuration before beginning the lengthy run:

  ```sh
  make validate-config
  ```

## 3. Configure this event

- [ ] Set `customization_user` when the desktop account is not the UID 1000
      account.
- [ ] Review `hardening_allowed_tcp_ports` and
      `hardening_allowed_udp_ports`. Leave both empty unless this machine must
      accept a host service from the event network. Each listed port is allowed
      on every interface from any IPv4 or IPv6 source.
- [ ] Review `docker_group_users`. Membership grants root-equivalent access to
      the Docker socket; leave the list empty to require `sudo docker`.
- [ ] Decide whether to enable each large optional profile:

  ```yaml
  tooling_bloodhound_enabled: false
  tooling_mobile_android_studio_enabled: false
  tooling_mobile_android_licenses_accepted: false
  offline_mirror_enabled: false
  ```

- [ ] If Android Studio is enabled, review the Android SDK license and set
      `tooling_mobile_android_licenses_accepted: true` only after accepting it.
- [ ] If the offline mirror is enabled, review its storage path and whether
      source packages or package-content indexes are needed. The default mirror
      stores Debian 13 amd64 and architecture-independent binary packages below
      `/srv/deburner/mirror`.
- [ ] Review any event VPN, proxy, DNS, CA certificate, radio, or network
      requirements that are outside these playbooks.

## 4. Provision while online

- [ ] Start provisioning as the normal desktop user:

  ```sh
  make
  ```

- [ ] Keep the machine online, powered on, and awake until Ansible finishes. If
      the mirror is enabled, wait for both archive synchronizations and Release
      signature checks.
- [ ] Require the final Ansible recap to show `failed=0` and `unreachable=0`.
      Resolve provisioning errors before exposing the machine to an event
      network.
- [ ] Retain the timestamped output under `.logs/` until preparation has been
      reviewed. Logs use mode `0600` and are ignored by Git.
- [ ] Confirm that `/var/lib/deburner/provision-manifest.json` exists. It records
      the installed package and tool inventory without configuration contents or
      user identity.

The preflight at the beginning of `make` stops before system changes unless the
machine is Debian 13 amd64 with systemd, its short host name is `deburner`, the
filesystem containing `/srv` has more than 400 GB free, and required Internet
services are reachable over validated HTTPS.

## 5. Reboot and verify

- [ ] Reboot once so group membership, GNOME settings, and the lid and power
      policy are effective:

  ```sh
  sudo reboot
  ```

- [ ] Log in to GNOME and run the optional read-only verification from the
      repository checkout:

  ```sh
  make verify
  ```

- [ ] Require the verification playbook to finish without failed checks. It
      works without Internet access and does not change the machine.
- [ ] Confirm that the intended GNOME account, keyboard layouts, display setup,
      audio, Wi-Fi, event VPN, and any required external adapters work.
- [ ] Close and reopen the login session if Docker group access was enabled,
      then confirm `docker info` works without `sudo`.
- [ ] Inspect the provisioning inventory when useful:

  ```sh
  python3 -m json.tool /var/lib/deburner/provision-manifest.json | less
  ```

## 6. Finish optional offline preparation

Skip this section when `offline_mirror_enabled` is `false`.

- [ ] If the mirror was not synchronized by the main run, synchronize it while
      the machine still has Internet access:

  ```sh
  make mirror-sync
  ```

- [ ] Refresh the mirror as close to disconnection as practical. Debian Release
      metadata expires, and the project does not bypass signature or expiry
      validation.
- [ ] Activate and validate the local package source only after synchronization
      succeeds:

  ```sh
  make mirror-enable
  deburner-apt-mode status
  ```

- [ ] Remember that this mirror contains Debian packages only. Upstream tools,
      container images, Git repositories, language package indexes, Android
      components, and other external artifacts must already be present before
      disconnecting.

APT can later be switched between the two source sets with
`sudo deburner-apt-mode offline` and `sudo deburner-apt-mode online`.

## 7. Final event readiness

- [ ] Confirm that automatic suspend is disabled and that closing the lid does
      not suspend the machine after the reboot.
- [ ] Confirm the date, time, time zone, battery health, charger, storage space,
      and event network details.
- [ ] Review listening sockets with `sudo ss -lntup`. SSH should not listen when
      `hardening_disable_ssh` has its default value of `true`.
- [ ] Start no C2 framework, Responder, BloodHound container, packet capture,
      wireless monitor mode, or listener until the authorized exercise requires
      it.
- [ ] Review Docker port publishing separately. Published container ports pass
      through forwarding and are not protected by the host input firewall.
- [ ] Remove personal secrets, account sessions, tokens, SSH keys, and unrelated
      removable media. Avoid signing in to personal services from the burner.
- [ ] Keep the burner away from trusted home, office, and personal-device
      networks after it joins the event environment.

## 8. End of event

- [ ] Do not reconnect the event-exposed installation to a trusted network or
      use it as evidence that another device is clean.
- [ ] Shut down the burner and boot a trusted firmware erase function or trusted
      maintenance environment.
- [ ] Verify the exact target device, then use the drive vendor's documented
      sanitize or secure-erase operation for its storage technology.
- [ ] Verify that the erase operation completed. File deletion, formatting,
      reinstalling Debian, or rerunning Ansible is not sufficient.
- [ ] Securely erase or discard any removable media that stored event data.
- [ ] Start the next event from another fresh Debian installation.
