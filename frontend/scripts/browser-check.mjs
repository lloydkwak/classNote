// Real Chromium + actual local APIs for the workspace/upload; status/error cases explicitly mocked.
// Dependencies live in ignored .cache/browser, leaving the application's dependency tree unchanged.
import { chromium } from '../../.cache/browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
import { mkdir,writeFile } from 'node:fs/promises'
import path from 'node:path'

const root=path.resolve(process.env.PHASE_E_ARTIFACTS??'data/phase-e-runs/frontend-01')
await mkdir(root,{recursive:true})
const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'})
const page=await context.newPage()
const base=process.env.FRONTEND_URL??'http://127.0.0.1:5177'
const job=process.env.TEST_JOB??'6c094c2fe7cc'
const cases=[],errors=[]
page.on('pageerror',error=>errors.push(error.message))
const check=async(name,callback)=>{try{await callback();cases.push({name,status:'passed'})}catch(error){cases.push({name,status:'failed',error:error.message});console.error(name,error.message)}}
const shot=name=>page.screenshot({path:path.join(root,name+'.png'),fullPage:true})
const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
const waitWorkspace=async()=>{await page.waitForSelector('.workspace .stage canvas',{timeout:30000});await page.waitForSelector('.workspace .ink',{timeout:30000});await page.waitForFunction(()=>!document.querySelector('.render-status'))}

try {
  await check('landing / responsive / accessible labels',async()=>{
    await page.goto(base)
    assert.equal(await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).isDisabled(),true)
    for(const width of [375,768,1280,1440]){
      await page.setViewportSize({width,height:1000});await noOverflow()
      await shot(width===375?'landing-mobile':width===1440?'landing-desktop':`landing-${width}`)
    }
    assert.equal(await page.getByLabel('PDF 슬라이드 선택').count(),1)
    assert.equal(await page.getByLabel('강의 녹음 선택').count(),1)
  })
  await check('file select / replace / delete / invalid file / failed upload',async()=>{
    await page.getByLabel('PDF 슬라이드 선택').setInputFiles({name:'wrong.txt',mimeType:'text/plain',buffer:Buffer.from('wrong')})
    await page.getByRole('alert').waitFor();assert.match(await page.getByRole('alert').innerText(),/PDF/)
    await page.getByLabel('PDF 슬라이드 선택').setInputFiles(path.resolve('data/phase-d-runs/gpu-e2e-03/fixture/synthetic.pdf'))
    await page.getByLabel('PDF 슬라이드 선택').setInputFiles({name:'replacement.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF fixture')})
    assert.equal(await page.getByText('replacement.pdf',{exact:true}).count(),1)
    await page.getByRole('button',{name:'PDF 삭제',exact:true}).click()
    assert.equal(await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).isDisabled(),true)
    await page.getByLabel('PDF 슬라이드 선택').setInputFiles(path.resolve('data/phase-d-runs/gpu-e2e-03/fixture/synthetic.pdf'))
    await page.getByLabel('강의 녹음 선택').setInputFiles(path.resolve('data/phase-d-runs/gpu-e2e-03/fixture/recording.wav'))
    assert.equal(await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).isEnabled(),true)
    await page.route('**/api/jobs',r=>r.fulfill({status:500,contentType:'application/json',body:'{"detail":"private traceback"}'}))
    await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).click()
    await page.getByRole('alert').waitFor();assert.doesNotMatch(await page.locator('body').innerText(),/private traceback/)
    await shot('upload-error');await page.unroute('**/api/jobs')
  })
  await check('actual multipart job creation / progress / result / refresh',async()=>{
    await page.getByRole('button',{name:'AI 필기 생성하기',exact:true}).click()
    await page.waitForURL(/#\/jobs\/[\w-]+/,{timeout:30000})
    await page.waitForSelector('.workspace',{timeout:60000})
    const hash=await page.evaluate(()=>location.hash)
    await page.reload();await page.waitForSelector('.workspace',{timeout:30000})
    assert.equal(await page.evaluate(()=>location.hash),hash)
  })
  await check('processing state / reload / error redaction / retry (mocked status)',async()=>{
    let failed=false,retried=0
    await page.route('**/api/jobs/progress-fixture',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:failed?'error':'transcribing',error:failed?'Traceback SECRET_TEST':null,chunks_processed:0,chunks_received:0,note_count:0})}))
    await page.route('**/api/jobs/progress-fixture/retry',r=>{retried++;failed=false;return r.fulfill({status:202,contentType:'application/json',body:'{"job_id":"progress-fixture"}'})})
    await page.goto(base+'/#/jobs/progress-fixture');await page.waitForSelector('.process-steps .active')
    await shot('processing');await page.reload();await page.waitForSelector('.process-steps .active')
    failed=true;await page.reload();await page.getByRole('button',{name:'다시 시도',exact:true}).waitFor()
    assert.doesNotMatch(await page.locator('body').innerText(),/SECRET_TEST|Traceback/)
    await shot('error-state');await page.getByRole('button',{name:'다시 시도',exact:true}).click()
    await page.waitForSelector('.process-steps .active');assert.equal(retried,1)
  })
  await check('actual lecture workspace / navigation / original / zoom / density / style / download',async()=>{
    await page.goto(base+'/#/jobs/'+job);await waitWorkspace()
    await page.getByRole('button',{name:'5쪽으로 이동',exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('.canvas-caption')?.textContent?.startsWith('5쪽'))
    await page.getByRole('button',{name:'원본',exact:true}).click();assert.equal(await page.locator('.stage .ink').count(),0)
    await page.getByRole('button',{name:'AI 필기',exact:true}).click();await page.waitForSelector('.stage .ink')
    await page.getByRole('button',{name:'확대',exact:true}).click();await page.getByRole('button',{name:'120%',exact:true}).click()
    await page.getByRole('button',{name:'자세하게',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.render-status'))
    await page.getByLabel('펜 스타일').selectOption('clean');await page.waitForFunction(()=>!document.querySelector('.render-status'))
    await page.getByLabel('펜 스타일').selectOption('natural');await page.waitForFunction(()=>!document.querySelector('.render-status'))
    const href=await page.getByRole('link',{name:'PDF 다운로드'}).getAttribute('href')
    const response=await context.request.get(base+href);assert.equal(response.status(),200)
    assert.equal((await response.body()).subarray(0,4).toString(),'%PDF')
    assert.equal(await page.getByRole('button',{name:'필기 재생: Inverse of z = α + βj is -α - βj',exact:true}).count(),1)
  })
  await check('actual audio seek / play / pause / source evidence',async()=>{
    await page.getByRole('button',{name:'전체 강의 재생',exact:true}).click()
    await page.waitForFunction(()=>{const a=document.querySelector('audio');return a&&!a.paused})
    await page.getByRole('button',{name:'일시정지',exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('audio')?.paused)
    const timeline=page.getByRole('slider',{name:'강의 재생 위치'})
    await timeline.focus();await page.keyboard.press('End')
    await page.waitForFunction(()=>document.querySelector('audio')?.currentTime>250)
    await page.getByRole('button',{name:'4쪽으로 이동',exact:true}).click()
    await page.waitForSelector('.source-status');assert.match(await page.locator('.source-status').innerText(),/근거 검증/)
    await page.getByRole('button',{name:'6쪽으로 이동',exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('.source-status')?.textContent?.includes('추출을 완료하지 못했습니다'))
  })
  await check('workspace screenshots / responsive / canvas and SVG frames',async()=>{
    await page.getByRole('button',{name:'5쪽으로 이동',exact:true}).click()
    for(const width of [1440,1280,768,375]){
      await page.setViewportSize({width,height:1000});await noOverflow()
      await page.waitForTimeout(200)
      await shot(width===1440?'result-desktop':width===375?'result-mobile':`result-${width}`)
      const dimensions=await page.evaluate(()=>{const s=document.querySelector('.stage .sheet').getBoundingClientRect();const v=document.querySelector('.stage svg.ink').getBoundingClientRect();return [s.width,s.height,v.width,v.height]})
      assert.ok(Math.abs(dimensions[0]-dimensions[2])<1&&Math.abs(dimensions[1]-dimensions[3])<1)
    }
    await page.getByRole('button',{name:'상세 노트',exact:true}).click();await noOverflow();await shot('result-mobile-notes')
    await page.getByRole('button',{name:'페이지',exact:true}).click();await noOverflow();await shot('result-mobile-pages')
  })
  assert.deepEqual(errors,[])
} finally {
  await writeFile(path.join(root,'report.json'),JSON.stringify({cases,page_errors:errors,passed:cases.filter(c=>c.status==='passed').length,failed:cases.filter(c=>c.status==='failed').length},null,2))
  await browser.close()
}
console.log(JSON.stringify(cases.map(c=>({name:c.name,status:c.status}))))
if(cases.some(c=>c.status==='failed')||errors.length)process.exitCode=1
