#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <pwd.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct fixture_pam {struct pam_conv conv;};static struct fixture_pam h;
uid_t fixture_getuid(void){return 1000;}uid_t fixture_geteuid(void){return 1000;}
int fixture_getpwuid_r(uid_t uid,struct passwd *pwd,char *buf,size_t size,struct passwd **out){
 (void)uid;(void)buf;(void)size;pwd->pw_name="fixture-user";*out=pwd;return 0;
}
int pam_start(const char *service,const char *user,const struct pam_conv *conv,pam_handle_t **out){
 struct rlimit limit;getrlimit(RLIMIT_CORE,&limit);
 if(strcmp(service,"omarchy-himmelblau-pin-change")||strcmp(user,"fixture-user")||prctl(PR_GET_DUMPABLE)!=0||limit.rlim_cur!=0)return PAM_SYSTEM_ERR;
 h.conv=*conv;*out=&h;return PAM_SUCCESS;
}
int pam_chauthtok(pam_handle_t *handle,int flags){
 (void)flags;const struct pam_message messages[]={
 {PAM_TEXT_INFO,"Use number 42 in Authenticator <b>literal</b>"},
 {PAM_PROMPT_ECHO_OFF,"New PIN:"},{PAM_PROMPT_ECHO_OFF,"Confirm PIN:"},
 {PAM_PROMPT_ECHO_ON,"One-time code:"},{PAM_ERROR_MSG,"A literal test error"}};
 for(int i=0;i<5;i++){
  const struct pam_message *msg=&messages[i];struct pam_response *answer=NULL;
  int result=handle->conv.conv(1,&msg,&answer,handle->conv.appdata_ptr);
  if(result!=PAM_SUCCESS)return result;
  if(i>0&&i<4){if(!answer[0].resp||strcmp(answer[0].resp,i==3?"test-otp":"p\xc3\xa4ss")){free(answer[0].resp);free(answer);return PAM_SYSTEM_ERR;}free(answer[0].resp);}
  free(answer);
 }
 /* Module stdout/stderr must never be forwarded to Quickshell logs. */
 puts("test-secret-not-a-protocol-message");fputs("test-secret-not-a-log\n",stderr);
 return PAM_SUCCESS;
}
int pam_end(pam_handle_t *handle,int result){(void)handle;(void)result;return PAM_SUCCESS;}
