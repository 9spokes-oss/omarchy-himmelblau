/* NSS module for the PAM stack harness: resolves the accounts in ACCOUNTS_PATH
 * (passwd format) as non-local users, the way Himmelblau's NSS module resolves
 * Entra accounts. Their shadow entry has the password "x", as Himmelblau's does,
 * so pam_unix can never authenticate them. Listed after "files" in nsswitch.conf,
 * so setuid helpers such as unix_chkpwd see the same accounts as the driver. */
#define _GNU_SOURCE
#include <errno.h>
#include <nss.h>
#include <pwd.h>
#include <shadow.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ACCOUNTS_PATH "/etc/entrastub/passwd"

struct account { char name[128], gecos[128], dir[256], shell[128]; uid_t uid; gid_t gid; };

static int parse(const char *line, struct account *a)
{
    unsigned long uid, gid;
    return sscanf(line, "%127[^:]:%*[^:]:%lu:%lu:%127[^:]:%255[^:]:%127[^\n]",
                  a->name, &uid, &gid, a->gecos, a->dir, a->shell) == 6
        && (a->uid = (uid_t)uid, a->gid = (gid_t)gid, 1);
}

static int find(const char *name, uid_t uid, int by_uid, struct account *a)
{
    char line[1024];
    FILE *f = fopen(ACCOUNTS_PATH, "r");
    if (f == NULL)
        return 0;
    while (fgets(line, sizeof line, f) != NULL)
        if (parse(line, a) && (by_uid ? a->uid == uid : !strcmp(a->name, name))) {
            fclose(f);
            return 1;
        }
    fclose(f);
    return 0;
}

static char *put(char **buf, size_t *left, const char *s)
{
    size_t n = strlen(s) + 1;
    if (n > *left)
        return NULL;
    char *p = memcpy(*buf, s, n);
    *buf += n;
    *left -= n;
    return p;
}

static enum nss_status fill_pw(const struct account *a, struct passwd *pw, char *buf, size_t len, int *err)
{
    if (!(pw->pw_name = put(&buf, &len, a->name)) || !(pw->pw_passwd = put(&buf, &len, "x"))
        || !(pw->pw_gecos = put(&buf, &len, a->gecos)) || !(pw->pw_dir = put(&buf, &len, a->dir))
        || !(pw->pw_shell = put(&buf, &len, a->shell))) {
        *err = ERANGE;
        return NSS_STATUS_TRYAGAIN;
    }
    pw->pw_uid = a->uid;
    pw->pw_gid = a->gid;
    return NSS_STATUS_SUCCESS;
}

enum nss_status _nss_entrastub_getpwnam_r(const char *name, struct passwd *pw, char *buf, size_t len, int *err)
{
    struct account a;
    if (!find(name, 0, 0, &a))
        return NSS_STATUS_NOTFOUND;
    return fill_pw(&a, pw, buf, len, err);
}

enum nss_status _nss_entrastub_getpwuid_r(uid_t uid, struct passwd *pw, char *buf, size_t len, int *err)
{
    struct account a;
    if (!find(NULL, uid, 1, &a))
        return NSS_STATUS_NOTFOUND;
    return fill_pw(&a, pw, buf, len, err);
}

enum nss_status _nss_entrastub_getspnam_r(const char *name, struct spwd *sp, char *buf, size_t len, int *err)
{
    struct account a;
    if (!find(name, 0, 0, &a))
        return NSS_STATUS_NOTFOUND;
    memset(sp, 0, sizeof *sp);
    if (!(sp->sp_namp = put(&buf, &len, a.name)) || !(sp->sp_pwdp = put(&buf, &len, "x"))) {
        *err = ERANGE;
        return NSS_STATUS_TRYAGAIN;
    }
    sp->sp_lstchg = -1;
    sp->sp_min = -1;
    sp->sp_max = -1;
    sp->sp_warn = -1;
    sp->sp_inact = -1;
    sp->sp_expire = -1;
    sp->sp_flag = ~0UL;
    return NSS_STATUS_SUCCESS;
}
