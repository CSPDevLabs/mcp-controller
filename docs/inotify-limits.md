# Troubleshooting: Too Many Open Files (inotify limits)

When running services that monitor a large number of files or directories (e.g., via `systemd`, `systemctl start`, or live-reloading development servers), you might encounter the following error:

```text
Failed to allocate directory watch: Too many open files
```

This means your system has exhausted its limit for `inotify` watches or instances.


## Checking Current Limits

You can check your currently active `inotify` limits using `sysctl`:

```bash
sysctl fs.inotify.max_user_instances
sysctl fs.inotify.max_user_watches
```

## Check Total Allocation

The total open instances across all processes with this command:

```bash
sudo find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l
```

Sums up the total active watches:

```bash
sudo find /proc/*/fd -lname anon_inode:inotify -printf '%hinfo/%f\n' 2>/dev/null \
  | sudo xargs grep -c '^inotify wd' 2>/dev/null \
  | awk -F':' '{sum += $2} END {print "Total inotify watches in use: " sum}'
```

Who is using watches?

```bash
sudo find /proc/*/fd -lname anon_inode:inotify -printf '%hinfo/%f\n' 2>/dev/null \
  | sudo xargs grep -c '^inotify wd' 2>/dev/null \
  | awk -F':' '{ split($1, a, "/"); sum[a[3]] += $2 } END { for (pid in sum) if (sum[pid]>0) print sum[pid], pid }' \
  | sort -nr | head -n 10 \
  | while read count pid; do echo "$count watches - $(cat /proc/$pid/comm 2>/dev/null) (PID: $pid)"; done
```

## Temporary Fix

To immediately increase the limits and allow your services to start (resets on reboot), run:

```bash
sudo sysctl fs.inotify.max_user_instances=8192
sudo sysctl fs.inotify.max_user_watches=524288
```

## Permanent Fix

To make the increased limits persist across system reboots, write the configuration to a new file in `/etc/sysctl.d/`:

```bash
echo "fs.inotify.max_user_instances=8192" | sudo tee -a /etc/sysctl.d/90-inotify.conf
echo "fs.inotify.max_user_watches=524288" | sudo tee -a /etc/sysctl.d/90-inotify.conf
```

Then, load the new settings:

```bash
sudo sysctl --system
```
