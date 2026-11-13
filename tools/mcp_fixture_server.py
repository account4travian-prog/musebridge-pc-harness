"""Synthetic local stdio server; no account, browser, network or personal data."""
import asyncio
from pathlib import Path
from mcp.server import MCPServer

server=MCPServer('MuseBridge fixture',log_level='ERROR')

@server.tool()
def echo(text:str)->str:return 'fixture:'+text

@server.tool()
def fail()->str:raise ValueError('intentional fixture failure')

@server.tool()
async def uncertain(marker:str)->str:
    with Path(marker).open('a',encoding='utf-8') as stream:stream.write('x')
    await asyncio.sleep(60)
    return 'too late'

if __name__=='__main__':server.run(transport='stdio')
