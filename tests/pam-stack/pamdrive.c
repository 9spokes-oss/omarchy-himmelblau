/* PAM stack harness driver.
 *
 *   pamdrive SERVICE USER OP...
 *
 * OP is auth, account, chauthtok or session. Each runs against one PAM handle,
 * in order, and prints "result OP CODE" (the PAM_* name without the prefix).
 * Conversation messages print as "prompt TEXT", "info TEXT" or "error TEXT".
 * Prompts are answered from PAMDRIVE_ANSWERS (comma-separated, the last answer
 * repeats); with no answers left a prompt fails the conversation. */
#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *answers[16];
static int answer_count, next_answer;

static int conv(int n, const struct pam_message **msg, struct pam_response **resp, void *data)
{
    (void)data;
    struct pam_response *r = calloc((size_t)n, sizeof *r);
    if (r == NULL)
        return PAM_BUF_ERR;
    for (int i = 0; i < n; i++) {
        switch (msg[i]->msg_style) {
        case PAM_PROMPT_ECHO_OFF:
        case PAM_PROMPT_ECHO_ON:
            printf("prompt %s\n", msg[i]->msg);
            if (answer_count == 0) {
                free(r);
                return PAM_CONV_ERR;
            }
            r[i].resp = strdup(answers[next_answer < answer_count ? next_answer : answer_count - 1]);
            next_answer++;
            break;
        case PAM_TEXT_INFO:
            printf("info %s\n", msg[i]->msg);
            break;
        default:
            printf("error %s\n", msg[i]->msg);
        }
    }
    *resp = r;
    return PAM_SUCCESS;
}

/* Skip pam_unix's failure delay: the harness runs thousands of failures. */
static void no_delay(int status, unsigned int delay, void *data)
{
    (void)status; (void)delay; (void)data;
}

static const char *name(int code)
{
    static const struct { int code; const char *name; } names[] = {
        {PAM_SUCCESS, "SUCCESS"}, {PAM_AUTH_ERR, "AUTH_ERR"}, {PAM_PERM_DENIED, "PERM_DENIED"},
        {PAM_USER_UNKNOWN, "USER_UNKNOWN"}, {PAM_SERVICE_ERR, "SERVICE_ERR"},
        {PAM_SYSTEM_ERR, "SYSTEM_ERR"}, {PAM_MAXTRIES, "MAXTRIES"},
        {PAM_AUTHINFO_UNAVAIL, "AUTHINFO_UNAVAIL"}, {PAM_ACCT_EXPIRED, "ACCT_EXPIRED"},
        {PAM_NEW_AUTHTOK_REQD, "NEW_AUTHTOK_REQD"}, {PAM_AUTHTOK_ERR, "AUTHTOK_ERR"},
        {PAM_AUTHTOK_RECOVERY_ERR, "AUTHTOK_RECOVERY_ERR"}, {PAM_TRY_AGAIN, "TRY_AGAIN"},
        {PAM_CRED_INSUFFICIENT, "CRED_INSUFFICIENT"}, {PAM_CONV_ERR, "CONV_ERR"},
        {PAM_SESSION_ERR, "SESSION_ERR"}, {PAM_ABORT, "ABORT"}, {PAM_IGNORE, "IGNORE"},
        {PAM_MODULE_UNKNOWN, "MODULE_UNKNOWN"}, {PAM_AUTHTOK_LOCK_BUSY, "AUTHTOK_LOCK_BUSY"},
    };
    for (size_t i = 0; i < sizeof names / sizeof names[0]; i++)
        if (names[i].code == code)
            return names[i].name;
    return "OTHER";
}

int main(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr, "usage: pamdrive SERVICE USER OP...\n");
        return 2;
    }
    char *list = getenv("PAMDRIVE_ANSWERS");
    if (list != NULL && *list) {
        list = strdup(list);
        for (char *a = strtok(list, ","); a != NULL && answer_count < 16; a = strtok(NULL, ","))
            answers[answer_count++] = a;
    }
    struct pam_conv c = {conv, NULL};
    pam_handle_t *pamh = NULL;
    int rc = pam_start(argv[1], argv[2], &c, &pamh);
    if (rc != PAM_SUCCESS) {
        printf("result start %s\n", name(rc));
        return 1;
    }
    pam_set_item(pamh, PAM_FAIL_DELAY, (const void *)no_delay);
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "auth"))
            rc = pam_authenticate(pamh, 0);
        else if (!strcmp(argv[i], "account"))
            rc = pam_acct_mgmt(pamh, 0);
        else if (!strcmp(argv[i], "chauthtok"))
            rc = pam_chauthtok(pamh, 0);
        else if (!strcmp(argv[i], "session"))
            rc = pam_open_session(pamh, 0);
        else {
            fprintf(stderr, "unknown op %s\n", argv[i]);
            return 2;
        }
        printf("result %s %s\n", argv[i], name(rc));
        fflush(stdout);
    }
    pam_end(pamh, rc);
    return 0;
}
