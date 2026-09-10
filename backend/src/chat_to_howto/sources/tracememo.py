"""Locked v2.3.0 TraceMemo read-only adapter."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Protocol
from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope
from chat_to_howto.security.secrets import SecretStore
from chat_to_howto.sources.base import *
class _Transport(Protocol):
 def request(self,method:str,path:str,*,headers:dict[str,str],params:Mapping[str,object]|None=None,json:object|None=None)->dict[str,Any]:...
class TraceMemoAdapter:
 def __init__(self,*,transport:_Transport,secret_store:SecretStore,token_access:SecretAccessScope,token_consent:ConsentRecord,account_id:str)->None:self._transport,self._store,self._access,self._consent,self._account,self._cancelled=transport,secret_store,token_access,token_consent,account_id,False
 def health(self)->SourceHealth:
  data=self._transport.request("GET","/api/v1/health",headers={})
  if data.get("ok") is not True or not isinstance(data.get("ready"),bool):raise UpstreamProtocolError("Source response did not match the locked contract.")
  return SourceHealth(True,data["ready"])
 def list_accounts(self)->tuple[str,...]:return (self._account,)
 def list_conversations(self)->list[ConversationRef]:
  contacts=self._get("/api/v1/contact",{}).get("contacts")
  if not isinstance(contacts,list):raise UpstreamProtocolError("Source response did not match the locked contract.")
  out=[]
  for item in contacts:
   if isinstance(item,dict):
    ident=item.get("md5")
    if isinstance(ident,(str,int)):out.append(ConversationRef("wechat",self._account,str(ident),str(item.get("m_nsNickName") or ident),str(item.get("type") or "unknown")))
  return out
 def fetch_range(self,conversation_id:str,start_ms:int,end_ms:int)->FetchPage:
  if any(not isinstance(x,int) or isinstance(x,bool) for x in (start_ms,end_ms)) or end_ms<=start_ms:raise ValueError("range must use increasing integer milliseconds")
  if self._cancelled:raise FetchCancelled("Source fetch cancelled locally.")
  data=self._get("/api/v1/chatlog",{"talker":conversation_id,"startTime":start_ms//1000,"endTime":(end_ms+999)//1000}); raw=data.get("messages")
  if not isinstance(raw,list):raise UpstreamProtocolError("Source response did not match the locked contract.")
  result=[]
  for item in raw:
   if isinstance(item,dict):
    value=item.get("createTime",item.get("datetime")); stamp=value if isinstance(value,int) and not isinstance(value,bool) else None
    if stamp is not None and stamp<100_000_000_000:stamp*=1000
    ident=item.get("id"); msg=SourceMessage(str(ident) if isinstance(ident,(str,int)) else None,conversation_id,stamp,item.get("content") if isinstance(item.get("content"),str) else None,len(result))
    if stamp is not None and start_ms<=stamp<end_ms:result.append(msg)
  return FetchPage(tuple(result),None,CompletenessEvidence("unknown","TraceMemo chatlog has no HTTP pagination or completeness signal."))
 def cancel(self)->None:self._cancelled=True
 def _get(self,path:str,params:Mapping[str,object])->dict[str,Any]:
  token=self._store.get(access=self._access,consent=self._consent); value=self._transport.request("GET",path,headers={"Authorization":f"Bearer {token}"},params=params)
  if not isinstance(value,dict):raise UpstreamProtocolError("Source response did not match the locked contract.")
  return value
