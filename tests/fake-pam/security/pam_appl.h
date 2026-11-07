#ifndef FIXTURE_PAM_H
#define FIXTURE_PAM_H
#define PAM_SUCCESS 0
#define PAM_SYSTEM_ERR 4
#define PAM_BUF_ERR 5
#define PAM_CONV_ERR 19
#define PAM_PROMPT_ECHO_OFF 1
#define PAM_PROMPT_ECHO_ON 2
#define PAM_ERROR_MSG 3
#define PAM_TEXT_INFO 4
typedef struct fixture_pam pam_handle_t;
struct pam_message { int msg_style; const char *msg; };
struct pam_response { char *resp; int resp_retcode; };
struct pam_conv {int (*conv)(int,const struct pam_message **,struct pam_response **,void*);void *appdata_ptr;};
int pam_start(const char*,const char*,const struct pam_conv*,pam_handle_t**);
int pam_chauthtok(pam_handle_t*,int);
int pam_end(pam_handle_t*,int);
#endif
