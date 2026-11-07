/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 9Spokes
 * Fixed, root-managed principal binding shared by the two PIN recovery clients.
 * NSS is trusted: round trips detect drift, not global UID uniqueness.
 */
#ifndef OMARCHY_RECOVERY_ACCOUNT_H
#define OMARCHY_RECOVERY_ACCOUNT_H
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#ifndef OH_RECOVERY_DIR
#define OH_RECOVERY_DIR "/etc/omarchy-himmelblau"
#endif
#ifndef OH_RECOVERY_OWNER
#define OH_RECOVERY_OWNER 0
#endif
struct oh_identity { char upn[257]; char name[257]; uid_t uid; };
static inline int oh_same_identity(const struct oh_identity *left, const struct oh_identity *right) {
    return left->uid == right->uid && !strcmp(left->upn, right->upn) && !strcmp(left->name, right->name);
}
static inline int oh_safe_identity_field(const char *value) {
    size_t n = strlen(value);
    if (!n || n > 256 || value[0] == '-' || value[0] == '#' || value[0] == '+') return 0;
    for (size_t i = 0; i < n; ++i)
        if (!((value[i] >= 'a' && value[i] <= 'z') || (value[i] >= 'A' && value[i] <= 'Z')
            || (value[i] >= '0' && value[i] <= '9') || strchr("._@-", value[i]))) return 0;
    return 1;
}
/* Reads the binding ("UPN\nname\nuid\n") and checks that `requested` is its
 * UPN or Unix name. No NSS lookups: safe on a daemon's main loop. */
static inline int oh_read_recovery_account(const char *requested, struct oh_identity *identity) {
    int ok = 0, fd = -1, dir = open(OH_RECOVERY_DIR, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat st; char data[600];
    char *name, *number, *end; ssize_t length; unsigned long value;
    if (dir < 0 || fstat(dir, &st) || st.st_uid != OH_RECOVERY_OWNER || (st.st_mode & 07777) != 0755) goto done;
    fd = openat(dir, "recovery-account", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != OH_RECOVERY_OWNER
        || (st.st_mode & 07777) != 0644 || st.st_size <= 0 || st.st_size >= (off_t)sizeof(data)) goto done;
    length = read(fd, data, sizeof(data));
    if (length != st.st_size || memchr(data, 0, (size_t)length)) goto done;
    data[length] = 0;
    name = strchr(data, '\n'); if (!name) goto done; *name++ = 0;
    number = strchr(name, '\n'); if (!number) goto done; *number++ = 0;
    end = strchr(number, '\n'); if (!end || end[1]) goto done; *end = 0;
    if (!oh_safe_identity_field(data) || !strchr(data, '@') || !oh_safe_identity_field(name)
        || !requested || (strcmp(requested, data) && strcmp(requested, name))) goto done;
    if (!*number) goto done;
    for (const char *p = number; *p; ++p) if (*p < '0' || *p > '9') goto done;
    errno = 0; value = strtoul(number, &end, 10);
    if (errno || *end || value < 1000 || value >= (unsigned long)(uid_t)-1) goto done;
    identity->uid = (uid_t)value;
    strcpy(identity->upn, data); strcpy(identity->name, name);
    ok = 1;
done:
    if (fd >= 0) close(fd);
    if (dir >= 0) close(dir);
    if (!ok) memset(identity, 0, sizeof(*identity));
    return ok;
}
/* oh_read_recovery_account(), then the caller check (unless privileged) and
 * NSS round trips that must all resolve to the bound UID and name. */
static inline int oh_recovery_account(const char *requested, uid_t caller, int privileged,
                                      struct oh_identity *identity) {
    char nss[16384]; struct passwd record, *found = NULL;
    if (!oh_read_recovery_account(requested, identity)) return 0;
    if ((privileged || caller == identity->uid)
        && !getpwnam_r(identity->upn, &record, nss, sizeof(nss), &found) && found
        && record.pw_uid == identity->uid && !strcmp(record.pw_name, identity->name)
        && !getpwnam_r(identity->name, &record, nss, sizeof(nss), &found) && found
        && record.pw_uid == identity->uid && !strcmp(record.pw_name, identity->name)
        && !getpwuid_r(identity->uid, &record, nss, sizeof(nss), &found) && found
        && record.pw_uid == identity->uid && !strcmp(record.pw_name, identity->name)) return 1;
    memset(identity, 0, sizeof(*identity));
    return 0;
}
#endif
