const HANDLES = ['GGzy55', 'gat_only_you', 'seyyxz'];
const LIMIT = 600;
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'} });
const month = now => new Date(now + 9 * 3600000).toISOString().slice(0, 7);
const security = response => {
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'no-store');
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('Referrer-Policy', 'no-referrer');
  headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  return new Response(response.body, {status:response.status, headers});
};
async function authorized(request, env) {
  if (!env.DASHBOARD_PASSWORD || env.DASHBOARD_PASSWORD.length < 16) return false;
  const auth = request.headers.get('Authorization') || '';
  if (!auth.startsWith('Basic ')) return false;
  let decoded;
  try { decoded = atob(auth.slice(6)); } catch { return false; }
  const expected = 'admin:' + env.DASHBOARD_PASSWORD;
  const digest = value => crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  const [a,b] = await Promise.all([digest(decoded), digest(expected)]);
  const x=new Uint8Array(a), y=new Uint8Array(b);
  let difference=0;
  for (let i=0;i<x.length;i++) difference |= x[i]^y[i];
  return difference===0;
}
const empty = () => ({accounts: HANDLES.map(handle => ({handle, followers:null, name:handle, posts:[], updated:null, error:null})), records:[], usage:0, limit:LIMIT, keyPresent:false, mode:'cloud'});

// Refreshes are performed by the scheduled handler only. Opening the dashboard
// never calls the provider and cannot increase collection costs.
export async function refresh(env, now = Date.now(), fetcher = fetch) {
  if (!env.DASHBOARD || !env.TWITTERAPI_IO_KEY) return {skipped:'missing configuration'};
  const lastAttempt = Number(await env.DASHBOARD.get('last-attempt') || 0);
  if (now - lastAttempt < 4 * 3600000) return {skipped:'recent attempt'};
  await env.DASHBOARD.put('last-attempt', String(now));
  const old = await env.DASHBOARD.get('snapshot', 'json') || empty();
  const usageKey = 'usage:' + month(now);
  let usage = Number(await env.DASHBOARD.get(usageKey) || 0);
  async function provider(path, handle) {
    if (usage >= LIMIT) throw Error('월 API 요청 상한에 도달했습니다.');
    usage++;
    await env.DASHBOARD.put(usageKey, String(usage));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetcher('https://api.twitterapi.io' + path + '?userName=' + handle, {headers:{'X-API-Key':env.TWITTERAPI_IO_KEY}, signal:controller.signal});
      if (!response.ok) throw Error('데이터 제공사 오류 HTTP ' + response.status);
      const result = await response.json();
      if (result.status==='error') throw Error('데이터 제공사가 요청을 처리하지 못했습니다.');
      return result;
    } finally { clearTimeout(timeout); }
  }
  const accounts=[];
  for (const handle of HANDLES) {
    const previous = old.accounts.find(a=>a.handle===handle) || empty().accounts.find(a=>a.handle===handle);
    const account = {...previous, error:null};
    const errors=[];
    try {
      const result = await provider('/twitter/user/info', handle);
      const data = result.data;
      if (!data || !data.id) throw Error('프로필 응답 형식을 확인해주세요.');
      account.followers=data.followers ?? null;
      account.name=data.name || handle;
    } catch (e) { errors.push(e.message.startsWith('데이터') || e.message.startsWith('월') || e.message.startsWith('프로필') ? e.message : '프로필 연결 실패'); }
    try {
      const result = await provider('/twitter/user/last_tweets', handle);
      const raw = result.tweets ?? result.data?.tweets;
      if (!Array.isArray(raw)) throw Error('최근 글 응답 형식을 확인해주세요.');
      account.posts=raw.slice(0,20).map(p=>({id:p.id, text:p.text || '', url:'https://x.com/'+handle+'/status/'+String(p.id), likes:p.likeCount || 0,replies:p.replyCount || 0,reposts:p.retweetCount || 0,created:p.createdAt || ''}));
      account.updated=now/1000;
    } catch (e) { errors.push(e.message.startsWith('데이터') || e.message.startsWith('월') || e.message.startsWith('최근') ? e.message : '최근 글 연결 실패'); }
    account.error=errors.join(' / ') || null;
    accounts.push(account);
  }
  await env.DASHBOARD.put('snapshot', JSON.stringify({accounts, collectedAt:now/1000}));
  return {usage, accounts:accounts.length};
}
export default {
  async fetch(request, env) {
    if (!env.DASHBOARD_PASSWORD || env.DASHBOARD_PASSWORD.length < 16) return json({error:'서버 비밀번호 설정이 필요합니다. 최소 16자의 ASCII 비밀번호를 등록해주세요.'},503);
    if (!(await authorized(request,env))) return new Response('로그인이 필요합니다.', {status:401,headers:{'WWW-Authenticate':'Basic realm="Signal dashboard", charset="UTF-8"','Cache-Control':'no-store'}});
    if (!env.DASHBOARD) return json({error:'저장소 연결이 필요합니다.'},503);
    const url=new URL(request.url);
    if (url.pathname==='/api/dashboard' && request.method==='GET') {
      const snapshot=await env.DASHBOARD.get('snapshot','json') || empty();
      const records=await env.DASHBOARD.get('records','json') || [];
      const usage=Number(await env.DASHBOARD.get('usage:'+month(Date.now())) || 0);
      return security(json({...snapshot,records,usage,limit:LIMIT,keyPresent:!!env.TWITTERAPI_IO_KEY,mode:'cloud'}));
    }
    if (url.pathname==='/api/records' && request.method==='POST') {
      if (request.headers.get('Origin') !== url.origin) return json({error:'다른 사이트에서 보낸 요청입니다.'},403);
      if (!request.headers.get('Content-Type')?.startsWith('application/json')) return json({error:'JSON required'},415);
      if (Number(request.headers.get('Content-Length') || 0)>10000) return json({error:'요청이 너무 큽니다.'},413);
      try {
        const text=await request.text();
        if (text.length>10000) return json({error:'요청이 너무 큽니다.'},413);
        const value=JSON.parse(text);
        const content=String(value.text || '').trim();
        const link=String(value.url || '').trim();
        if (!HANDLES.includes(value.handle) || !content || content.length>3000 || !['크립토','일상','셀카','공부'].includes(value.category) || (link && !link.startsWith('https://x.com/'))) return json({error:'계정, 내용, 카테고리와 X 링크를 확인해주세요.'},400);
        const records=await env.DASHBOARD.get('records','json') || [];
        records.unshift({id:crypto.randomUUID(),handle:value.handle,text:content,category:value.category,url:link,created:Date.now()/1000});
        await env.DASHBOARD.put('records',JSON.stringify(records.slice(0,100)));
        return security(json({ok:true},201));
      } catch { return json({error:'기록 저장에 실패했습니다.'},400); }
    }
    if (request.method!=='GET' && request.method!=='HEAD') return json({error:'Method not allowed'},405);
    if (url.pathname.startsWith('/api/')) return json({error:'Not found'},404);
    return security(await env.ASSETS.fetch(request));
  },
  async scheduled(event,env,ctx) { ctx.waitUntil(refresh(env)); }
};
