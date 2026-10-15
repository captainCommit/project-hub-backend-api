// k6 load test: simulated users browsing the deployed app. Read-only (GET requests only).
//
//   k6 run -e BASE_URL=https://d7edvr3wd741d.cloudfront.net -e ACCOUNT_ID=<uuid> \
//          -e SESSION=<ph_session cookie value> [-e MAX_USERS=100] scripts/loadtest.js
//
// SESSION: sign in, then copy the ph_session cookie from DevTools > Application > Cookies. It expires
// with the access token (about an hour), so take a fresh one per run.
// Each simulated user opens a page (a parallel burst of API calls, like the browser does), reads it for
// 5–15 s, and opens another. Users ramp up in steps so the summary shows where latency starts to climb.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'https://d7edvr3wd741d.cloudfront.net').replace(/\/$/, '');
const ACCOUNT_ID = __ENV.ACCOUNT_ID;
const SESSION = __ENV.SESSION;
const MAX_USERS = Number(__ENV.MAX_USERS || 50);

if (!ACCOUNT_ID || !SESSION) {
  throw new Error('Set ACCOUNT_ID and SESSION (see the header comment).');
}

const throttled = new Rate('throttled');

const step = (fraction) => Math.max(1, Math.round(MAX_USERS * fraction));

export const options = {
  scenarios: {
    browsing: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '1m', target: step(0.1) },
        { duration: '2m', target: step(0.1) },
        { duration: '1m', target: step(0.25) },
        { duration: '2m', target: step(0.25) },
        { duration: '1m', target: step(0.5) },
        { duration: '2m', target: step(0.5) },
        { duration: '1m', target: MAX_USERS },
        { duration: '2m', target: MAX_USERS },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
    throttled: ['rate<0.01'],
  },
};

const params = {
  headers: { Cookie: `ph_session=${SESSION}`, Accept: 'application/json' },
  timeout: '30s',
};

const account = `${BASE_URL}/api/v1/accounts/${ACCOUNT_ID}`;

// The resource calendar needs a date range, as the UI sends: this month and the next two.
const today = new Date();
const isoDate = (date) => date.toISOString().slice(0, 10);
const calendarRange = `start_date=${isoDate(new Date(today.getFullYear(), today.getMonth(), 1))}`
  + `&end_date=${isoDate(new Date(today.getFullYear(), today.getMonth() + 3, 0))}`;

// Each page is the set of API calls the UI fires when it opens.
const PAGES = [
  ['home', [`${account}/me/home`, `${BASE_URL}/api/v1/notifications?page_size=30`, `${account}/sidebar`, `${account}/my-access`]],
  ['dashboard', [`${account}/dashboard`, `${account}/programs-projects`, `${account}/sidebar`]],
  ['my work', [`${account}/tasks/due`, `${account}/options`, `${account}/sidebar`]],
  ['resources', [`${account}/resources`, `${account}/resource-calendar?${calendarRange}`, `${account}/time-off`]],
  ['portfolios', [`${account}/portfolios`, `${account}/programs-projects`]],
];

export default function () {
  const [page, urls] = PAGES[Math.floor(Math.random() * PAGES.length)];
  const responses = http.batch(urls.map((url) => ['GET', url, null, { ...params, tags: { page } }]));

  for (const res of responses) {
    // Lambda throttling reaches the browser as a 5xx from API Gateway; 429 if API Gateway itself throttles.
    throttled.add(res.status === 429 || res.status === 503 || res.status === 500);
    check(res, {
      'status 2xx': (r) => r.status >= 200 && r.status < 300,
      'not signed out': (r) => r.status !== 401,
    });
  }

  sleep(5 + Math.random() * 10);
}
