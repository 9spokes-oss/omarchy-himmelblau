/* Stand-in for pam_himmelblau.so in the PAM stack harness, and, built with
 * STAGE_PREFIX="homed-" and its own LOG_PATH, for pam_systemd_home.so.
 *
 * Each call is appended to LOG_PATH as "service user stage", then answered from
 * CONTROL_PATH, whose lines read "user stage RESULT" with stage one of auth,
 * account, prelim, update, session (prefixed with STAGE_PREFIX). A user/stage
 * pair with no line returns PAM_IGNORE, which is what Himmelblau returns for a
 * name it doesn't know. */
#define _GNU_SOURCE
#include <security/pam_modules.h>
#include <security/pam_ext.h>
#include <stdio.h>
#include <string.h>

#define CONTROL_PATH "/etc/pamstub/control"
#ifndef LOG_PATH
#define LOG_PATH "/run/pamstub/calls"
#endif
#ifndef STAGE_PREFIX
#define STAGE_PREFIX ""
#endif

static const struct { const char *name; int code; } results[] = {
    {"SUCCESS", PAM_SUCCESS}, {"AUTH_ERR", PAM_AUTH_ERR}, {"IGNORE", PAM_IGNORE},
    {"USER_UNKNOWN", PAM_USER_UNKNOWN}, {"SERVICE_ERR", PAM_SERVICE_ERR},
    {"PERM_DENIED", PAM_PERM_DENIED}, {"NEW_AUTHTOK_REQD", PAM_NEW_AUTHTOK_REQD},
    {"AUTHINFO_UNAVAIL", PAM_AUTHINFO_UNAVAIL}, {"ACCT_EXPIRED", PAM_ACCT_EXPIRED},
    {"AUTHTOK_ERR", PAM_AUTHTOK_ERR}, {"CRED_INSUFFICIENT", PAM_CRED_INSUFFICIENT},
};

static int answer(pam_handle_t *pamh, const char *bare_stage)
{
    const void *service = NULL;
    const char *user = NULL;
    char line[512], u[256], s[32], r[32], stage[32];
    int code = PAM_IGNORE;

    snprintf(stage, sizeof stage, "%s%s", STAGE_PREFIX, bare_stage);

    if (pam_get_item(pamh, PAM_SERVICE, &service) != PAM_SUCCESS || service == NULL)
        service = "?";
    if (pam_get_user(pamh, &user, NULL) != PAM_SUCCESS || user == NULL)
        return PAM_SERVICE_ERR;

    FILE *log = fopen(LOG_PATH, "a");
    if (log != NULL) {
        fprintf(log, "%s %s %s\n", (const char *)service, user, stage);
        fclose(log);
    }

    FILE *control = fopen(CONTROL_PATH, "r");
    if (control == NULL)
        return PAM_IGNORE;
    while (fgets(line, sizeof line, control) != NULL) {
        if (sscanf(line, "%255s %31s %31s", u, s, r) != 3 || strcmp(u, user) || strcmp(s, stage))
            continue;
        code = PAM_SERVICE_ERR;
        for (size_t i = 0; i < sizeof results / sizeof results[0]; i++)
            if (!strcmp(r, results[i].name))
                code = results[i].code;
    }
    fclose(control);
    return code;
}

int pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags; (void)argc; (void)argv;
    return answer(pamh, "auth");
}

int pam_sm_setcred(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}

int pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags; (void)argc; (void)argv;
    return answer(pamh, "account");
}

int pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)argc; (void)argv;
    if (flags & PAM_PRELIM_CHECK)
        return answer(pamh, "prelim");
    if (flags & PAM_UPDATE_AUTHTOK)
        return answer(pamh, "update");
    return PAM_SERVICE_ERR;
}

int pam_sm_open_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags; (void)argc; (void)argv;
    return answer(pamh, "session");
}

int pam_sm_close_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}
