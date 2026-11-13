# MB-26 shell fluency probe

No default shell change is made by this probe.

Decision after scoring: retain PowerShell as the default. PowerShell and installed
Git Bash each completed all five routine jobs with zero failed calls and zero
failed commands. The intentional exit 3 in the stop-on-failure chain was expected
and passed. Git Bash remains selectable per command. Both runs used fresh
disposable chats, one existing identity lock, and hidden processes; no uncertain
send was replayed.

| Shell | Status | `shell.run` calls | Failed calls | Failed commands | Job records | Result |
|---|---:|---:|---:|---:|---:|---:|
| powershell | completed | 5 | 0 | 0 | 5 | True |
| bash | completed | 5 | 0 | 0 | 5 | True |

Check results:
- powershell / command_chain: True
- powershell / command_count: True
- powershell / command_env: True
- powershell / command_find: True
- powershell / command_python: True
- powershell / exactly_five_calls_and_jobs: True
- powershell / exit_chain: True
- powershell / exit_count: True
- powershell / exit_env: True
- powershell / exit_find: True
- powershell / exit_python: True
- powershell / file_chain_marker_absent: True
- powershell / file_find_output: True
- powershell / file_line_count_output: True
- powershell / file_one_command_environment: True
- powershell / file_python_output: True
- powershell / file_seed_unchanged: True
- powershell / jobs_in_project: True
- powershell / no_failed_shell_run_calls: True
- powershell / no_other_write_tools: True
- powershell / no_unexpected_nonzero_jobs: True
- powershell / request_chain: True
- powershell / request_count: True
- powershell / request_env: True
- powershell / request_find: True
- powershell / request_python: True
- bash / command_chain: True
- bash / command_count: True
- bash / command_env: True
- bash / command_find: True
- bash / command_python: True
- bash / exactly_five_calls_and_jobs: True
- bash / exit_chain: True
- bash / exit_count: True
- bash / exit_env: True
- bash / exit_find: True
- bash / exit_python: True
- bash / file_chain_marker_absent: True
- bash / file_find_output: True
- bash / file_line_count_output: True
- bash / file_one_command_environment: True
- bash / file_python_output: True
- bash / file_seed_unchanged: True
- bash / jobs_in_project: True
- bash / no_failed_shell_run_calls: True
- bash / no_other_write_tools: True
- bash / no_unexpected_nonzero_jobs: True
- bash / request_chain: True
- bash / request_count: True
- bash / request_env: True
- bash / request_find: True
- bash / request_python: True
