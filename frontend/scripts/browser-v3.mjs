// Real API/PDF/audio from the isolated comparison job. Upload/progress/error requests
// are explicitly intercepted to avoid another paid STT/LLM run during browser regression.
import {chromium} from '../../.cache/browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
import {mkdir,writeFile} from 'node:fs/promises'
import path from 'node:path'
const root=path.resolve(process.env.V3_ARTIFACTS??'data/annotation-v3-runs/comparison-02/frontend')
await mkdir(root,{recursive:true})
const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'})
const page=await context.newPage(),cases=[],errors=[]
const base=process.env.FRONTEND_URL??'http://127.0.0.1:5181',job=process.env.TEST_JOB??'05a433631737'
page.on('pageerror',e=>errors.push(e.message))
const check=async(name,fn)=>{try{await fn();cases.push({name,status:'passed'})}catch(e){cases.push({name,status:'failed',error:e.message});await shot('failed-'+name.slice(0,20).replace(/[^a-z0-9]/gi,'-'));console.error(name,e.message,await page.locator('.canvas-caption').allTextContents())}}
const shot=name=>page.screenshot({path:path.join(root,name+'.png'),fullPage:true})
const painted=async()=>{await page.waitForSelector('.render-status',{state:'detached',timeout:90000});await page.waitForFunction(()=>{const c=document.querySelector('.stage canvas');if(!c||c.width<10||c.height<10)return false;const x=c.getContext('2d');if(!x)return false;const v=x.getImageData(Math.floor(c.width/2),Math.floor(c.height*.1),1,1).data;return v[3]===255&&Math.min(v[0],v[1],v[2])<240},{},{timeout:90000})}
const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
try{
 await check('mode selection / multipart / progress (intercepted)',async()=>{
  await page.goto(base);await page.getByText('필기 설정',{exact:true}).click()
  await page.getByLabel('필기 방식',{exact:true}).selectOption('visual_agent')
  await shot('landing-desktop')
  await page.getByLabel('PDF 슬라이드 선택').setInputFiles(path.resolve('data/annotation-v3-runs/comparison-01/state/jobs/'+job+'/input.pdf'))
  await page.getByLabel('강의 녹음 선택').setInputFiles(path.resolve('data/annotation-v3-runs/comparison-01/state/jobs/'+job+'/audio.mp3'))
  let modeSent=false
  await page.route('**/api/jobs',route=>{const body=route.request().postData()??'';modeSent=/name="annotation_mode"\r\n\r\nvisual_agent/.test(body);return route.fulfill({json:{job_id:'pending-v3'}})})
  await page.route('**/api/jobs/pending-v3',route=>route.fulfill({json:{status:'writing_notes',error:null}}))
  await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).click()
  await page.waitForSelector('.processing-card');assert.ok(modeSent)
  await shot('processing');await page.unroute('**/api/jobs');await page.unroute('**/api/jobs/pending-v3')
 })
 await check('actual study Overview / PDF / refresh',async()=>{
  await page.goto(base+'/#/jobs/'+job);await page.waitForSelector('.study-overview',{timeout:45000})
  await page.waitForSelector('.stage canvas',{timeout:45000});await painted()
  assert.match(await page.locator('.study-overview').innerText(),/대표 예제/)
  assert.equal(await page.getByRole('button',{name:'Overview 열기',exact:true}).getAttribute('aria-current'),'page')
  await shot('overview-desktop');await page.reload();await page.waitForSelector('.study-overview',{timeout:45000})
 })
 await check('source slide / evidence / density / style / download (actual API)',async()=>{
  await page.getByRole('button',{name:'4쪽으로 이동',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('.canvas-caption')?.textContent?.startsWith('4쪽'))
  await page.waitForSelector('.stage .ink');await page.waitForSelector('.detailed-notes article')
  await Promise.all([page.waitForResponse(r=>r.url().includes('/annotations?')&&r.url().includes('density=detailed')&&r.url().includes('ink_style=natural')&&r.status()===200,{timeout:90000}),page.getByRole('button',{name:'자세하게',exact:true}).first().click()])
  await page.waitForFunction(()=>!document.querySelector('.render-status'))
  await Promise.all([page.waitForResponse(r=>r.url().includes('/annotations?')&&r.url().includes('ink_style=clean')&&r.status()===200,{timeout:90000}),page.getByLabel('펜 스타일',{exact:true}).selectOption('clean')]);await painted()
  const href=await page.getByRole('link',{name:'PDF 다운로드',exact:true}).getAttribute('href')
  const r=await context.request.get(base+href);assert.equal(r.status(),200);assert.equal((await r.body()).subarray(0,4).toString(),'%PDF')
  await painted();await shot('result-desktop')
 })
 await check('source audio seek / play / pause (actual audio)',async()=>{
  await page.getByRole('button',{name:'전체 강의 재생',exact:true}).click()
  await page.waitForFunction(()=>{const a=document.querySelector('audio');return a&&!a.paused})
  await page.getByRole('button',{name:'일시정지',exact:true}).click()
  await page.getByRole('slider',{name:'강의 재생 위치'}).focus();await page.keyboard.press('End')
  await page.waitForFunction(()=>document.querySelector('audio')?.currentTime>250)
 })
 await check('375/768/1280/1440 responsive and Overview source mapping',async()=>{
  for(const width of [1440,1280,768,375]){await page.setViewportSize({width,height:1000});await noOverflow();await painted();await shot(width===375?'result-mobile':'result-'+width)}
  await page.getByRole('button',{name:'페이지',exact:true}).click();await page.getByRole('button',{name:'Overview 열기',exact:true}).click()
  await page.waitForSelector('.study-overview',{state:'attached'})
  await page.waitForFunction(()=>document.querySelector('.canvas-caption')?.textContent?.startsWith('Lecture Overview'))
  await painted();await shot('overview-mobile')
 })
 await check('error / retry (intercepted)',async()=>{
  await page.route('**/api/jobs/error-v3',r=>r.fulfill({json:{status:'error',error:'private backend traceback'}}))
  await page.route('**/api/jobs/error-v3/retry',r=>r.fulfill({status:202,json:{job_id:'error-v3'}}))
  await page.goto(base+'/#/jobs/error-v3');await page.getByRole('button',{name:'다시 시도',exact:true}).waitFor()
  assert.doesNotMatch(await page.locator('body').innerText(),/private backend traceback/)
  await shot('error-state');await page.getByRole('button',{name:'다시 시도',exact:true}).click()
 })
}finally{await browser.close();await writeFile(path.join(root,'browser-report.json'),JSON.stringify({cases,errors,paid_calls_from_browser:0,upload_progress_error_intercepted:true},null,2))}
console.log(JSON.stringify({cases,errors},null,2));if(cases.some(c=>c.status==='failed')||errors.length)process.exitCode=1
