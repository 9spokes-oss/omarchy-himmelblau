#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <pwd.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "recovery-account.h"

/* Same limits as the SDDM helper: 4096 UTF-16 code units are at most 12288
 * UTF-8 bytes; longer PAM text is cut to MESSAGE_MAX bytes. */
#define RESPONSE_MAX (3 * 4096)
#define MESSAGE_MAX 8192
static FILE *protocol;
static unsigned messages;
/* SIGTERM asks the helper to stop: no further prompts and no PIN change are
 * started, and PAM still ends normally so its buffers are released. */
static volatile sig_atomic_t stopping;
static void stop(int signal) { (void)signal; stopping = 1; }

static void quoted(const char *text, size_t length) {
    fputc('"', protocol);
    for (size_t i = 0; i < length; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == '"' || c == '\\') { fputc('\\', protocol); fputc(c, protocol); }
        else if (c < 0x20) fprintf(protocol, "\\u%04x", c);
        else fputc(c, protocol);
    }
    fputc('"', protocol);
}
static int message(const char *kind, const char *text, int visible) {
    if (!text || ++messages > 256) return -1;
    size_t length = strnlen(text, MESSAGE_MAX);
    /* When cutting, do not split a UTF-8 sequence. */
    while (length && text[length] && ((unsigned char)text[length] & 0xC0) == 0x80) length--;
    fprintf(protocol, "{\"type\":\"%s\",\"visible\":%s,\"text\":", kind, visible ? "true" : "false");
    quoted(text, length); fputs("}\n", protocol);
    return fflush(protocol);
}
static int hex(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static char *response(void) {
    char encoded[RESPONSE_MAX * 3 + 12] = {0};
    char decoded[RESPONSE_MAX + 1] = {0};
    size_t n = 0, used = 0;
    int c, valid = 0;
    while ((c = fgetc(stdin)) != EOF && c != '\n') {
        if (n >= sizeof(encoded) - 1) goto done;
        encoded[n++] = (char)c;
    }
    if (c != '\n' || n < 9 || memcmp(encoded, "response ", 9)) goto done;
    for (size_t i = 9; i < n; i++) {
        unsigned char value = (unsigned char)encoded[i];
        if (value == '%') {
            if (i + 2 >= n || hex(encoded[i+1]) < 0 || hex(encoded[i+2]) < 0) goto done;
            value = (unsigned char)((hex(encoded[i+1]) << 4) | hex(encoded[i+2])); i += 2;
        } else if (value < 0x21 || value > 0x7e) goto done;
        if (!value || used >= RESPONSE_MAX) goto done;
        decoded[used++] = (char)value;
    }
    valid = 1;
done:;
    char *result = valid ? strndup(decoded, used) : NULL;
    explicit_bzero(encoded, sizeof(encoded)); explicit_bzero(decoded, sizeof(decoded));
    return result;
}
static int conversation(int count, const struct pam_message **input,
                        struct pam_response **output, void *unused) {
    (void)unused;
    if (count < 1 || count > 16 || !input || !output) return PAM_CONV_ERR;
    struct pam_response *answers = calloc((size_t)count, sizeof(*answers));
    if (!answers) return PAM_BUF_ERR;
    for (int i = 0; i < count; i++) {
        if (stopping || !input[i]) goto failed;
        int style = input[i]->msg_style;
        if (style == PAM_PROMPT_ECHO_OFF || style == PAM_PROMPT_ECHO_ON) {
            if (message("prompt", input[i]->msg, style == PAM_PROMPT_ECHO_ON)) goto failed;
            answers[i].resp = response();
            if (!answers[i].resp) goto failed;
        } else if (style == PAM_TEXT_INFO || style == PAM_ERROR_MSG) {
            if (message(style == PAM_ERROR_MSG ? "error" : "info", input[i]->msg, 0)) goto failed;
        } else goto failed;
    }
    *output = answers; return PAM_SUCCESS; /* PAM owns successful response buffers. */
failed:
    for (int i = 0; i < count; i++) if (answers[i].resp) {
        explicit_bzero(answers[i].resp, strlen(answers[i].resp)); free(answers[i].resp);
    }
    free(answers); return PAM_CONV_ERR;
}
int main(int argc, char **argv) {
    (void)argv;
    struct rlimit no_core = {0, 0};
    if (argc != 1 || getuid() == 0 || getuid() != geteuid() || getgid() != getegid()
        || setrlimit(RLIMIT_CORE, &no_core) || prctl(PR_SET_DUMPABLE, 0)) return 2;
    /* No SA_RESTART: a blocked read, or a module's wait, returns EINTR. */
    struct sigaction action = {0};
    action.sa_handler = stop;
    if (sigemptyset(&action.sa_mask) || sigaction(SIGTERM, &action, NULL)) return 2;
    /* Responses are secrets: keep them out of a stdio buffer that is never wiped. */
    if (setvbuf(stdin, NULL, _IONBF, 0)) return 2;
    int channel = dup(STDOUT_FILENO), sink = open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (channel < 0 || sink < 0 || fcntl(channel, F_SETFD, FD_CLOEXEC) < 0
        || dup2(sink, STDOUT_FILENO) < 0 || dup2(sink, STDERR_FILENO) < 0) return 2;
    close(sink);
    protocol = fdopen(channel, "w"); if (!protocol) return 2;
    char buffer[16384]; struct passwd account, *found = NULL;
    if (getpwuid_r(getuid(), &account, buffer, sizeof(buffer), &found) || !found) return 2;
    struct oh_identity identity;
    if (!oh_recovery_account(account.pw_name, getuid(), 0, &identity)) return 2;
    struct pam_conv conv = {conversation, NULL}; pam_handle_t *handle = NULL;
    int status = pam_start("omarchy-himmelblau-pin-change", identity.upn, &conv, &handle);
    if (status == PAM_SUCCESS) status = pam_acct_mgmt(handle, 0);
    const void *pam_user = NULL;
    if (status == PAM_SUCCESS && (pam_get_item(handle, PAM_USER, &pam_user) != PAM_SUCCESS
        || !pam_user || strcmp(pam_user, identity.upn))) status = PAM_SYSTEM_ERR;
    struct oh_identity current;
    if (status == PAM_SUCCESS && (!oh_recovery_account(identity.upn, getuid(), 0, &current)
        || !oh_same_identity(&identity, &current))) status = PAM_SYSTEM_ERR;
    if (status == PAM_SUCCESS && stopping) status = PAM_SYSTEM_ERR;
    if (status == PAM_SUCCESS) status = pam_chauthtok(handle, 0);
    if (handle) pam_end(handle, status);
    explicit_bzero(buffer, sizeof(buffer));
    fprintf(protocol, "{\"type\":\"result\",\"success\":%s}\n", status == PAM_SUCCESS ? "true" : "false");
    int failed = fflush(protocol); fclose(protocol);
    return status == PAM_SUCCESS && !failed ? 0 : 1;
}
