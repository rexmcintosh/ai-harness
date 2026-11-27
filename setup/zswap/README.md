# zswap

Compressed cache in front of the 4 GiB swapfile on bebop-vm. Idle memory pages are
compressed (zstd) and kept in RAM instead of going to disk. The pool may use up to
20% of RAM and only grows when needed. Each cached page still holds a swapfile slot,
so the swapfile size is still the overflow limit. earlyoom and the memory-guard caps
are unchanged.

zswap is built into the kernel (`CONFIG_ZSWAP=y`). zram was rejected because it needs
`linux-modules-extra`, which the `linux-virtual` kernel package does not install.
Plan checked by Astra 6.2 on 2026-11-26: no `Before=swap.target` (ordering cycle),
values set before `enabled`, gradual undo.

Install (root):

    install -m 755 setup/zswap/zswap-settings /usr/local/sbin/zswap-settings
    install -m 644 setup/zswap/zswap-settings.service /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now zswap-settings.service

Check: `grep -E '^Zswap' /proc/meminfo`, `grep -E '^(pswpin|pswpout|zswpin|zswpout|zswpwb) ' /proc/vmstat`.

Undo: `systemctl disable zswap-settings.service`, then
`echo 0 > /sys/module/zswap/parameters/enabled`. Cached pages drain over time.
Do not run `swapoff` to force it: that pulls swapped pages back into RAM.
