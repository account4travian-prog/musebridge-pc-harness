"""Small acknowledged chat headers; ordinary messages remain unchanged."""
import hashlib
import json


def _clean(value):
    return ' '.join(str(value or '').split())


def _clip(value, budget):
    value = _clean(value)
    if len(value.encode('utf-8')) <= budget:return value
    marker = '...'
    while value and len((value+marker).encode('utf-8')) > budget:value=value[:-1]
    return value+marker


def fingerprint(cid, project, policy, generation, tools, skill_version):
    identity = [cid,project,policy,generation,sorted(tools),skill_version]
    return hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=True).encode()).hexdigest()


class HeaderManager:
    def __init__(self):
        self.repeat_every = 0
        self.states = {}

    def reset(self):self.states.clear()

    def needs_header(self,cid,stamp):
        previous = self.states.get(cid)
        if previous is None or previous['stamp'] != stamp:return True
        return self.repeat_every > 0 and previous['since_header'] >= self.repeat_every - 1

    def acknowledge(self,cid,stamp,had_header):
        previous = self.states.get(cid)
        since = 0 if had_header or previous is None else previous['since_header'] + 1
        self.states[cid] = {'stamp':stamp,'since_header':since}


def format_header(cid,project,policy,shells,summary,tools,skill_version):
    if project:
        location = f'Project "{_clip(project["name"],75)}" at {_clip(project["root"],150)}'
        git = _clip(summary.get('branch') or summary.get('status_summary') or 'unavailable',55)
        status = _clip(summary.get('status_summary'),45)
        if status and summary.get('branch'):git += ', '+status
        location += '; Git '+git
    else:
        location = 'No workspace selected. Choose a workspace in MuseBridge for PC commands and files.'
    profile = {'project':'Project work','read_only':'Read only','desktop':'Desktop control'}.get(policy['profile'],policy['profile'])
    power = shells.get('powershell',{})
    shell = 'PowerShell '+_clip(power.get('version') or 'available',40)
    if shells.get('bash',{}).get('available'):shell += ' (Git Bash available)'
    groups = []
    for prefix in ('file','shell','plan','task','browser','desktop','terminal','mcp','artifact','screen','change'):
        if any(name.startswith(prefix+'.') for name in tools):groups.append(prefix)
    lines = ['[MuseBridge] Windows PC. '+location+'.',
        'Profile: '+profile+(' - STOPPED locally' if policy.get('paused') else '')+'. Shell: '+shell+'.',
        'Chat: '+_clip(cid,70)+'. Tools: '+','.join(groups)+'. Skill: gadget-musebridge-windows-pc v'+str(skill_version)+'.',
        'Read bridge.context for project instructions, plan and tasks before PC work.']
    text = '\n'.join(lines)
    # Each field is separately bounded, so headers stay short without deleting
    # the workflow instruction or returning an apparently complete long path.
    if len(text.encode('utf-8')) > 780:
        lines[0] = '[MuseBridge] Windows PC. '+('Project "'+_clip(project['name'],55)+'"; full path in bridge.context.' if project else location)
        text = '\n'.join(lines)
    return text
