#!/usr/bin/env node
/**
 * Sogni AI Video Hook Generator - Bridge script called from Python.
 *
 * Usage:  node sogni_generate.js <prompt> <output_path> [width] [height] [frames] [fps]
 * Output: JSON to stdout  { "video_url": "...", "output_path": "..." }
 *         Logs go to stderr so Python can parse stdout cleanly.
 */

const { SogniClientWrapper } = require('@sogni-ai/sogni-client-wrapper');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
require('dotenv').config();

const log = (...args) => process.stderr.write(`[Sogni] ${args.join(' ')}\n`);

async function downloadFile(url, outputPath) {
  return new Promise((resolve, reject) => {
    const proto = url.startsWith('https') ? https : http;
    const file = fs.createWriteStream(outputPath);
    proto.get(url, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        file.close();
        fs.unlinkSync(outputPath);
        return downloadFile(response.headers.location, outputPath).then(resolve).catch(reject);
      }
      response.pipe(file);
      file.on('finish', () => { file.close(); resolve(outputPath); });
    }).on('error', (err) => {
      fs.unlink(outputPath, () => {});
      reject(err);
    });
  });
}

async function main() {
  const [,, prompt, outputPath, widthStr, heightStr, framesStr, fpsStr] = process.argv;

  if (!prompt || !outputPath) {
    log('Usage: node sogni_generate.js <prompt> <output_path> [width] [height] [frames] [fps]');
    process.exit(1);
  }

  const username = process.env.SOGNI_USERNAME;
  const password = process.env.SOGNI_PASSWORD;
  if (!username || !password) {
    log('ERROR: SOGNI_USERNAME and SOGNI_PASSWORD must be set in .env');
    process.exit(1);
  }

  const width  = parseInt(widthStr  || '480', 10);
  const height = parseInt(heightStr || '480', 10);
  const frames = parseInt(framesStr || '81',  10);
  const fps    = parseInt(fpsStr    || '16',  10);

  log(`Prompt: ${prompt.substring(0, 60)}...`);
  log(`Output: ${outputPath}`);
  log(`Config: ${width}x${height}, ${frames} frames, ${fps} fps`);

  const client = new SogniClientWrapper({
    username,
    password,
    appId: `scap-hook-${Date.now()}`,
    autoConnect: false,
    network: 'fast',
  });

  try {
    log('Connecting...');
    await client.connect();
    log('Connected');

    log('Generating video (this may take 1-3 minutes)...');
    const startTime = Date.now();

    const result = await client.createVideoProject({
      modelId: 'wan_v2.2-14b-fp8_t2v_lightx2v',
      positivePrompt: prompt,
      negativePrompt: 'text, watermark, blurry, low quality, distorted',
      frames,
      fps,
      steps: 4,
      width,
      height,
      numberOfMedia: 1,
      network: 'fast',
      tokenType: 'spark',
      outputFormat: 'mp4',
      waitForCompletion: true,
      timeout: 300000,
    });

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    log(`Video generated in ${elapsed}s`);

    if (!result.videoUrls || result.videoUrls.length === 0) {
      throw new Error('No video URL returned from Sogni');
    }

    const videoUrl = result.videoUrls[0];
    log(`Video URL: ${videoUrl}`);

    // Download to output path
    const dir = path.dirname(outputPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    log('Downloading video...');
    await downloadFile(videoUrl, outputPath);
    log(`Saved to: ${outputPath}`);

    // Output JSON to stdout for Python to parse
    const output = JSON.stringify({ video_url: videoUrl, output_path: outputPath });
    process.stdout.write(output);

  } catch (error) {
    log(`ERROR: ${error.message}`);
    if (error.code) log(`Code: ${error.code}`);
    process.exit(1);
  } finally {
    try {
      await client.disconnect();
      log('Disconnected');
    } catch (e) {
      log(`Disconnect error: ${e.message}`);
    }
  }
}

main().catch((err) => {
  log(`Fatal: ${err.message}`);
  process.exit(1);
});
