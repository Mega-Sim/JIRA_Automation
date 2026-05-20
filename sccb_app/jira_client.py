import requests
import threading
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
import re
import html as _html
import json
import time
from html.parser import HTMLParser
import time as _time


class _TableExtractor(HTMLParser):
    """Very small HTML table extractor (stdlib only).

    It collects tables as:
      tables = [ [ [cell, cell, ...], [cell, ...], ... ], ... ]
    """

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = 0
        self._in_tr = 0
        self._in_cell = 0
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell_chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._cur_table = []
        elif t == "tr" and self._in_table == 1:
            self._in_tr += 1
            if self._in_tr == 1:
                self._cur_row = []
        elif t in ("th", "td") and self._in_table == 1 and self._in_tr == 1:
            self._in_cell += 1
            if self._in_cell == 1:
                self._cur_cell_chunks = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("th", "td") and self._in_cell == 1:
            cell = "".join(self._cur_cell_chunks)
            cell = re.sub(r"\s+", " ", cell).strip()
            if self._cur_row is not None:
                self._cur_row.append(cell)
            self._cur_cell_chunks = []
            self._in_cell -= 1
        elif t == "tr" and self._in_tr == 1:
            if self._cur_table is not None and self._cur_row is not None:
                self._cur_table.append(self._cur_row)
            self._cur_row = None
            self._in_tr -= 1
        elif t == "table" and self._in_table == 1:
            if self._cur_table is not None:
                self.tables.append(self._cur_table)
            self._cur_table = None
            self._in_table -= 1

    def handle_data(self, data):
        if self._in_table == 1 and self._in_tr == 1 and self._in_cell == 1:
            self._cur_cell_chunks.append(data)


class JiraClient:
    def __init__(self, base_url: str, user: str, password: str, verify_ssl: bool = True, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.user = user or ""
        self.password = password or ""
        self.verify_ssl = bool(verify_ssl)
        self.timeout = int(timeout)

        # cache
        self._fields_cache = None
        self._field_id_cache = {}

        # thread-local Session (connection reuse)
        self._tls = threading.local()

    def _auth(self):
        return HTTPBasicAuth(self.user, self.password)

    def _session(self) -> requests.Session:
        s = getattr(self._tls, "session", None)
        if s is None:
            s = requests.Session()
            s.auth = self._auth()
            s.verify = self.verify_ssl
            adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32)
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            self._tls.session = s
        return s

    def get(self, path: str, params=None):
        r = self._session().get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json_body: dict, headers: dict | None = None):
        url = f"{self.base_url}{path}"
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)
        r = self._session().post(
            url,
            json=json_body,
            timeout=self.timeout,
            headers=h,
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.json() if r.text else None


    def put(self, path: str, json_body: dict, headers: dict | None = None):
        url = f"{self.base_url}{path}"
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)
        r = self._session().put(
            url,
            json=json_body,
            timeout=self.timeout,
            headers=h,
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.json() if r.text else None

    def update_issue_fields(self, issue_key: str, fields: dict):
        if not fields:
            return None
        path = f"/rest/api/2/issue/{issue_key}"
        return self.put(path, {"fields": fields})

    def get_text(self, path_or_url: str, params=None, headers: dict | None = None) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            p = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self.base_url}{p}"

        h = {"Accept": "*/*"}
        if headers:
            h.update(headers)

        r = self._session().get(
            url,
            params=params,
            timeout=self.timeout,
            headers=h,
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.text or ""

    @staticmethod
    def _extract_len_string(text: str) -> str:
        if not text:
            return ""
        m = re.search(r"(\d+)\s*자", text)
        if m:
            return f"{m.group(1)} 자"
        try:
            obj = json.loads(text)
        except Exception:
            return ""
        stack = [obj]
        seen = set()
        while stack:
            cur = stack.pop()
            cid = id(cur)
            if cid in seen:
                continue
            seen.add(cid)
            if isinstance(cur, str):
                m = re.search(r"(\d+)\s*자", cur)
                if m:
                    return f"{m.group(1)} 자"
            elif isinstance(cur, dict):
                for v in cur.values():
                    stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
        return ""

    @staticmethod
    def _adf_to_plain_text(adf) -> str:
        """Convert Jira description payload to plain text.

        Jira may return description as plain string or as an ADF-like JSON.
        We traverse common shapes and concatenate 'text' nodes.
        """
        if adf is None:
            return ""
        if isinstance(adf, str):
            return adf
        stack = []
        if isinstance(adf, dict):
            stack.append(adf)
        elif isinstance(adf, list):
            stack.extend(adf)
        else:
            return ""

        out: list[str] = []
        seen = set()
        while stack:
            cur = stack.pop()
            cid = id(cur)
            if cid in seen:
                continue
            seen.add(cid)

            if isinstance(cur, str):
                if cur:
                    out.append(cur)
                continue

            if isinstance(cur, dict):
                t = cur.get("text")
                if isinstance(t, str) and t:
                    out.append(t)

                for k in ("content", "items", "children"):
                    v = cur.get(k)
                    if isinstance(v, list):
                        stack.extend(v)
                    elif isinstance(v, dict):
                        stack.append(v)

                # fallback traversal
                for v in cur.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
                continue

            if isinstance(cur, list):
                stack.extend(cur)

        return "".join(out)

    @classmethod
    def calc_body_length_string_from_desc(cls, desc) -> str:
        plain = cls._adf_to_plain_text(desc)
        if not plain:
            return ""
        plain2 = re.sub(r"\s+", "", plain)
        return f"{len(plain2)} 자"

    @staticmethod
    def _extract_len_string_from_description_checker_html(text: str) -> str:
        if not text:
            return ""
        m = re.search(r"var\s+description\s*=\s*`([\s\S]*?)`;\s*", text)
        if not m:
            return ""
        desc = m.group(1)
        plain = re.sub(r"\s+", "", desc)
        return f"{len(plain)} 자"

    def get_body_length_string_from_ui(self, issue_key: str) -> str:
        """본문 길이 확인 - 최적화된 버전"""
        # 1차: Description_Checker API 시도 (가장 빠름)
        try:
            txt = self.get_text(
                "/rest/scriptrunner/latest/custom/Description_Checker",
                params={"issueKey": issue_key, "_": int(_time.time() * 1000)},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}/browse/{issue_key}",
                },
            )
            v = self._extract_len_string(txt)
            if v:
                return v
            v2 = self._extract_len_string_from_description_checker_html(txt)
            if v2:
                return v2
        except Exception:
            pass

        # 2차: browse HTML에서 "길이 확인" 링크 찾기
        try:
            browse_html = self.get_text(f"/browse/{issue_key}")
            if not browse_html:
                return ""

            candidates: list[str] = []
            for m in re.finditer(r'href="([^"]+)"[^>]*>\s*길이\s*확인\s*<', browse_html):
                candidates.append(m.group(1))
            
            # 가장 가능성 높은 후보만 시도
            if candidates:
                c = candidates[0]
                c = _html.unescape(c)
                c = c.replace("\\/", "/")
                if c.startswith("//"):
                    c = "https:" + c
                try:
                    panel_txt = self.get_text(c)
                    v = self._extract_len_string(panel_txt)
                    if v:
                        return v
                except Exception:
                    pass
        except Exception:
            pass
        
        return ""

    def _extract_tc_complete_count(self, txt: str) -> int:
        if not txt:
            return 0
        m = re.search(r"생성\s*완료\s*[:：]\s*(\d+)\s*건", txt)
        if not m:
            m = re.search(r"생성\s*완료[^0-9]{0,20}(\d+)\s*건", txt)
        if not m:
            return 0
        try:
            return int(m.group(1))
        except Exception:
            return 0



    DIFFICULTY_MIN_CASES = {
        "A": 8,
        "B": 6,
        "C": 4,
        "D": 2,
    }

    @staticmethod
    def _normalize_difficulty_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for k in ("value", "name", "label"):
                v = value.get(k)
                if isinstance(v, str) and v.strip():
                    value = v
                    break
        elif isinstance(value, list):
            for item in value:
                norm = JiraClient._normalize_difficulty_value(item)
                if norm:
                    return norm
            return ""
        s = str(value).strip().upper()
        m = re.search(r'([ABCD])', s)
        if m:
            return m.group(1)
        m = re.search(r'([ABCD])', s)
        return m.group(1) if m else ""

    def get_issue_difficulty(self, issue_key: str) -> str:
        field_id = self.find_field_id("난이도")
        if field_id:
            try:
                raw = self.get_issue_field(issue_key, field_id)
                diff = self._normalize_difficulty_value(raw)
                if diff:
                    return diff
            except Exception:
                pass

        # fallback: browse HTML 내 '난이도' 라벨 근처에서 A/B/C/D 추출
        try:
            html = self.get_text(f"/browse/{issue_key}")
            m = re.search(r"난이도[\s\S]{0,500}?>([ABCD])<", html, re.IGNORECASE)
            if m:
                return (m.group(1) or "").upper()
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_aio_task_refs_from_html(html_text: str) -> list[tuple[int, int]]:
        text = _html.unescape(html_text or "")
        if not text:
            return []
        text = text.replace('\\/', '/')

        refs: list[tuple[int, int]] = []
        seen = set()

        patterns = [
            r"/rest/aio-tcms/1\.0/project/(\d+)/traceability/task/(\d+)",
            r'"jiraProjectID"\s*:\s*(\d+)[\s\S]{0,400}?"taskID"\s*:\s*(\d+)',
            r'jiraProjectID%22%3A(\d+)[\s\S]{0,400}?taskID%22%3A(\d+)',
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                try:
                    ref = (int(m.group(1)), int(m.group(2)))
                except Exception:
                    continue
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)

        project_ids = []
        for m in re.finditer(r'jiraProjectID"\s*:\s*(\d+)', text, re.IGNORECASE):
            try:
                project_ids.append(int(m.group(1)))
            except Exception:
                pass
        project_id = project_ids[0] if project_ids else None
        if project_id is not None:
            for m in re.finditer(r'"taskID"\s*:\s*(\d+)', text, re.IGNORECASE):
                try:
                    ref = (project_id, int(m.group(1)))
                except Exception:
                    continue
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _get_aio_traceability_task_json(self, project_id: int, task_id: int):
        return self.get(
            f"/rest/aio-tcms/1.0/project/{int(project_id)}/traceability/task/{int(task_id)}",
            params={
                "c_pId": int(project_id),
                "t": int(_time.time() * 1000),
            },
        )

    def _get_issue_meta_for_aio(self, issue_key: str):
        data = self.get(
            f"/rest/api/2/issue/{issue_key}",
            params={"fields": "project"},
        ) or {}
        issue_id = str(data.get("id") or "").strip()
        fields = data.get("fields") or {}
        project = fields.get("project") or {}
        project_id = project.get("id")
        try:
            project_id = int(project_id)
        except Exception:
            project_id = None
        return issue_id, project_id

    def _get_aio_traceability_issue_json_candidates(self, issue_key: str, issue_id: str | None, project_id: int | None):
        issue_key = str(issue_key or "").strip()
        issue_id = str(issue_id or "").strip()
        paths = []
        seen = set()

        def add(path, params=None):
            key = (path, tuple(sorted((params or {}).items())))
            if key in seen:
                return
            seen.add(key)
            paths.append((path, params or None))

        # 실제 사내 AIO endpoint 변형을 폭넓게 시도
        if project_id is not None:
            base = f"/rest/aio-tcms/1.0/project/{int(project_id)}"
            add(f"{base}/traceability/issue/{issue_key}", {"c_pId": int(project_id), "t": int(_time.time() * 1000)})
            if issue_id:
                add(f"{base}/traceability/issue/{issue_id}", {"c_pId": int(project_id), "t": int(_time.time() * 1000)})
                add(f"{base}/traceability/jiraIssue/{issue_id}", {"c_pId": int(project_id), "t": int(_time.time() * 1000)})
                add(f"{base}/traceability/jiraissue/{issue_id}", {"c_pId": int(project_id), "t": int(_time.time() * 1000)})
                add(f"{base}/traceability/task/{issue_id}", {"c_pId": int(project_id), "t": int(_time.time() * 1000)})
            add(f"{base}/traceability", {"issueKey": issue_key, "c_pId": int(project_id), "t": int(_time.time() * 1000)})
            if issue_id:
                add(f"{base}/traceability", {"issueId": issue_id, "c_pId": int(project_id), "t": int(_time.time() * 1000)})
                add(f"{base}/traceability", {"taskId": issue_id, "c_pId": int(project_id), "t": int(_time.time() * 1000)})

        for path, params in paths:
            try:
                data = self.get(path, params=params)
            except Exception:
                continue
            if data:
                return data
        return None

    @staticmethod
    def _iter_text_values(obj):
        """dict/list/text payload를 재귀 순회하여 문자열 값을 모두 꺼낸다."""
        out = []
        stack = [obj]
        seen = set()
        while stack:
            cur = stack.pop()
            cid = id(cur)
            if cid in seen:
                continue
            seen.add(cid)
            if isinstance(cur, str):
                out.append(cur)
            elif isinstance(cur, (int, float, bool)):
                out.append(str(cur))
            elif isinstance(cur, dict):
                for k, v in cur.items():
                    out.append(str(k))
                    stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
        return out

    @staticmethod
    def _looks_like_aio_testcase_row(row) -> bool:
        if not isinstance(row, dict):
            return False
        keys = {str(k).lower() for k in row.keys()}
        joined = " ".join(JiraClient._iter_text_values(row))
        if re.search(r"[A-Z][A-Z0-9_]+-TC-\d+", joined):
            return True
        # AIO testcase row에서 흔한 컬럼/필드 조합. 단일 status만으로는 오탐 가능하므로 key/id/title/name 계열을 같이 본다.
        identity_keys = {"key", "testcasekey", "testcaseid", "test_case_id", "tcid", "id"}
        title_keys = {"title", "name", "summary", "testcasename", "test_case_name"}
        testcase_keys = {"testcase", "test_case", "testcases", "test"}
        return bool((identity_keys & keys) and (title_keys & keys or testcase_keys & keys))

    @staticmethod
    def _extract_aio_count_from_html(html_text: str) -> int | None:
        """AIO Tests 패널 HTML에서 테스트 케이스 개수 fallback 추출."""
        if not html_text:
            return None
        text = _html.unescape(html_text or "")
        text = text.replace("\\/", "/")
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain)

        # 화면 탭: 테스트 케이스 (10), Test Cases (10)
        patterns = [
            r"테스트\s*케이스\s*\(\s*(\d+)\s*\)",
            r"Test\s*Cases?\s*\(\s*(\d+)\s*\)",
            r"testCases?Count[\"'\s:=]+(\d+)",
            r"totalTests?[\"'\s:=]+(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, plain, re.IGNORECASE)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass

        keys = set(re.findall(r"[A-Z][A-Z0-9_]+-TC-\d+", text))
        if keys:
            return len(keys)
        return None

    @staticmethod
    def _extract_aio_cycle_totals_from_payload(data) -> dict[str, int]:
        """AIO payload에서 테스트 케이스 개수 추출.

        기존 구현은 `testCycle.summary.totalTests` 또는 최상위 `testCases`만 확인했다.
        실제 AIO 응답은 플러그인 버전/화면 호출 위치에 따라 rows/items/results/testCases 안에
        중첩되거나, totalTests/testCaseCount 같은 필드명으로 내려올 수 있어 전체 payload를 순회한다.
        중복 배열을 여러 번 더하지 않도록 cycle key가 없을 때는 최대값 1개만 사용한다.
        """
        if not data:
            return {}

        cycle_totals: dict[str, int] = {}
        no_cycle_max = 0

        def put(cycle_key, total):
            nonlocal no_cycle_max
            try:
                n = int(total)
            except Exception:
                return
            if n <= 0:
                return
            if cycle_key:
                ck = str(cycle_key)
                prev = cycle_totals.get(ck, 0)
                if n > prev:
                    cycle_totals[ck] = n
            else:
                if n > no_cycle_max:
                    no_cycle_max = n

        def cycle_from_obj(obj, current_cycle=None):
            if not isinstance(obj, dict):
                return current_cycle
            cycle = current_cycle
            for key_name in ("testCycle", "testcycle", "cycle", "testRun", "testrun"):
                tc = obj.get(key_name)
                if isinstance(tc, dict):
                    detail = tc.get("detail") if isinstance(tc.get("detail"), dict) else {}
                    cycle = (
                        detail.get("key")
                        or detail.get("id")
                        or tc.get("key")
                        or tc.get("ID")
                        or tc.get("id")
                        or tc.get("cycleKey")
                        or tc.get("cycleId")
                        or cycle
                    )
                    summary = tc.get("summary") if isinstance(tc.get("summary"), dict) else {}
                    for name in ("totalTests", "totalTest", "total", "count", "totalCount", "testCaseCount"):
                        if summary.get(name) is not None:
                            put(cycle, summary.get(name))
                        if tc.get(name) is not None:
                            put(cycle, tc.get(name))
            return cycle

        def list_is_testcase_list(lst) -> bool:
            if not isinstance(lst, list) or not lst:
                return False
            checked = [x for x in lst[:30] if isinstance(x, dict)]
            if not checked:
                return False
            hits = sum(1 for x in checked if JiraClient._looks_like_aio_testcase_row(x))
            return hits > 0

        def walk(obj, current_cycle=None):
            if isinstance(obj, list):
                if list_is_testcase_list(obj):
                    put(current_cycle, len(obj))
                for item in obj:
                    walk(item, current_cycle)
                return

            if not isinstance(obj, dict):
                return

            cycle = cycle_from_obj(obj, current_cycle)

            # 현재 dict의 직접 total/count 필드. key 이름에 testcase/test/total 계열이 있을 때만 후보로 본다.
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in ("totaltests", "totaltest", "testcasecount", "testcasescount", "totalcases", "totaltestcases"):
                    put(cycle, v)
                elif isinstance(v, dict) and kl in ("summary", "progress", "statistics", "stat"):
                    for name in ("totalTests", "totalTest", "testCaseCount", "totalTestCases"):
                        if v.get(name) is not None:
                            put(cycle, v.get(name))
                elif isinstance(v, list) and kl in ("testcases", "testcase", "tests", "test_case_list", "items", "rows", "results", "data"):
                    if list_is_testcase_list(v):
                        put(cycle, len(v))

            for v in obj.values():
                walk(v, cycle)

        walk(data)
        # cycle별 total이 잡힌 경우에는 cycle 정보 없는 중첩 배열은 중복 후보로 보고 더하지 않는다.
        if no_cycle_max > 0 and not cycle_totals:
            cycle_totals["no_cycle"] = no_cycle_max
        return cycle_totals

    def _get_aio_actual_count(self, issue_key: str, issue_id: str | None = None, project_id: int | None = None) -> tuple[int | None, dict[str, int]]:
        """AIO 테스트 케이스 실제 개수 조회.

        반환: (actual_count, 상세 cycle_totals)
        """
        cycle_totals: dict[str, int] = {}
        issue_payload = None

        if project_id is not None:
            try:
                issue_payload = self._get_aio_traceability_issue_json_candidates(issue_key, issue_id, project_id)
            except Exception:
                issue_payload = None
        if issue_payload:
            for k, v in self._extract_aio_cycle_totals_from_payload(issue_payload).items():
                cycle_totals[k] = max(cycle_totals.get(k, 0), int(v))

        html = ""
        if not cycle_totals:
            try:
                html = self.get_text(f"/browse/{issue_key}")
            except Exception:
                html = ""

            html_count = self._extract_aio_count_from_html(html)
            if html_count:
                cycle_totals["html"] = int(html_count)

            if not cycle_totals:
                refs = self._extract_aio_task_refs_from_html(html)
                for project_id2, task_id in refs:
                    try:
                        payload = self._get_aio_traceability_task_json(project_id2, task_id)
                    except Exception:
                        continue
                    for k, v in self._extract_aio_cycle_totals_from_payload(payload).items():
                        cycle_totals[k] = max(cycle_totals.get(k, 0), int(v))

        if not cycle_totals:
            return None, {}
        return int(sum(cycle_totals.values())), cycle_totals

    def get_aio_test_validation(self, issue_key: str) -> dict:
        issue_id = ""
        project_id = None
        try:
            issue_id, project_id = self._get_issue_meta_for_aio(issue_key)
        except Exception:
            issue_id, project_id = "", None

        actual, cycle_totals = self._get_aio_actual_count(issue_key, issue_id, project_id)

        diff = self.get_issue_difficulty(issue_key)
        required = self.DIFFICULTY_MIN_CASES.get(diff)

        # 테스트 케이스가 실제로 존재하면 난이도 필드 추출 실패만으로 FAIL 처리하지 않는다.
        # 이 경우 최소 개수 비교 기준이 없으므로 OK(actual/-)로 표시한다.
        if not diff or required is None:
            if actual is not None and int(actual) > 0:
                return {
                    "difficulty": diff,
                    "required": required,
                    "actual": int(actual),
                    "ok": True,
                    "status": f"OK({int(actual)}/-)",
                    "cycle_totals": cycle_totals,
                }
            return {
                "difficulty": diff,
                "required": required,
                "actual": None,
                "ok": False,
                "status": "ERR(NO LEVEL)",
                "cycle_totals": cycle_totals,
            }

        if actual is None:
            return {
                "difficulty": diff,
                "required": required,
                "actual": None,
                "ok": False,
                "status": f"ERR(API/-/{required})",
                "cycle_totals": cycle_totals,
            }

        ok = int(actual) >= int(required)
        status = ("OK" if ok else "FAIL") + f"({int(actual)}/{int(required)})"
        return {
            "difficulty": diff,
            "required": int(required),
            "actual": int(actual),
            "ok": ok,
            "status": status,
            "cycle_totals": cycle_totals,
        }

    @staticmethod
    def _parse_tc_generation_status(data=None, text: str = "") -> dict:
        """LLM TC 생성 History/CheckTCStatus 응답을 보수적으로 판정."""
        complete_cnt = 0
        in_progress = 0
        failed_cnt = 0

        def to_int(v):
            try:
                return int(v)
            except Exception:
                return None

        def is_complete_text(s: str) -> bool:
            t = str(s or "").strip().lower()
            if not t:
                return False
            bad_words = ("생성중", "진행중", "진행", "running", "progress", "fail", "failed", "error", "오류", "실패")
            if any(w in t for w in bad_words):
                return False
            good_words = ("결과 확인", "생성 완료", "완료", "complete", "completed", "done", "success", "succeeded", "성공")
            return any(w in t for w in good_words)

        def is_progress_text(s: str) -> bool:
            t = str(s or "").strip().lower()
            return bool(t) and any(w in t for w in ("생성중", "진행중", "running", "progress"))

        def is_fail_text(s: str) -> bool:
            t = str(s or "").strip().lower()
            return bool(t) and any(w in t for w in ("fail", "failed", "error", "오류", "실패"))

        def scan_obj(obj):
            nonlocal complete_cnt, in_progress, failed_cnt
            if isinstance(obj, dict):
                for k, v in obj.items():
                    kl = str(k).lower()
                    n = to_int(v)
                    if n is not None:
                        if kl in ("completecount", "completedcount", "complete_count", "completed_count", "successcount", "success_count"):
                            complete_cnt = max(complete_cnt, n)
                        elif kl in ("inprogresscount", "progresscount", "runningcount", "in_progress_count"):
                            in_progress = max(in_progress, n)
                        elif kl in ("failcount", "failedcount", "errorcount", "failurecount"):
                            failed_cnt = max(failed_cnt, n)
                    is_status_field = kl in ("status", "state", "result", "buttontext", "label", "상태")
                    if is_status_field:
                        if is_complete_text(v):
                            complete_cnt += 1
                        elif is_progress_text(v):
                            in_progress += 1
                        elif is_fail_text(v):
                            failed_cnt += 1
                    if not is_status_field:
                        scan_obj(v)
            elif isinstance(obj, list):
                for item in obj:
                    scan_obj(item)
            elif isinstance(obj, str):
                if is_complete_text(obj):
                    complete_cnt += 1
                elif is_progress_text(obj):
                    in_progress += 1
                elif is_fail_text(obj):
                    failed_cnt += 1

        if data is not None:
            scan_obj(data)
            if not text:
                try:
                    text = json.dumps(data, ensure_ascii=False)
                except Exception:
                    text = " ".join(JiraClient._iter_text_values(data))

        if text:
            plain = _html.unescape(str(text))
            plain = re.sub(r"<[^>]+>", " ", plain)
            plain = re.sub(r"\s+", " ", plain)

            # 화면 하단: 생성 요청 : 1건, 생성 완료 : 1건, 생성중 : 0건
            for pat in (r"생성\s*완료\s*[:：]?\s*(\d+)\s*건", r"완료\s*[:：]?\s*(\d+)\s*건"):
                m = re.search(pat, plain)
                if m:
                    n = to_int(m.group(1))
                    if n is not None:
                        complete_cnt = max(complete_cnt, n)
            for pat in (r"생성\s*중\s*[:：]?\s*(\d+)\s*건", r"생성중\s*[:：]?\s*(\d+)\s*건"):
                m = re.search(pat, plain)
                if m:
                    n = to_int(m.group(1))
                    if n is not None:
                        in_progress = max(in_progress, n)
            if complete_cnt <= 0 and is_complete_text(plain):
                complete_cnt = max(complete_cnt, 1)
            if complete_cnt <= 0 and in_progress <= 0 and is_progress_text(plain):
                in_progress = max(in_progress, 1)
            if complete_cnt <= 0 and failed_cnt <= 0 and is_fail_text(plain):
                failed_cnt = max(failed_cnt, 1)

        return {
            "complete_count": int(complete_cnt),
            "in_progress_count": int(in_progress),
            "failed_count": int(failed_cnt),
            "ok": int(complete_cnt) > 0,
        }

    def _post_tc_status_payload(self, issue_key: str):
        """CheckTCStatus 응답을 JSON 또는 text로 받아온다."""
        path = "/rest/scriptrunner/latest/custom/CheckTCStatus"
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/browse/{issue_key}",
        }
        r = self._session().post(
            url,
            json={"JiraKey": issue_key},
            timeout=self.timeout,
            headers=headers,
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        text = r.text or ""
        data = None
        if text:
            try:
                data = r.json()
            except Exception:
                data = None
        return data, text

    def get_tc_generation_check(self, issue_key: str) -> dict:
        data = None
        text = ""
        try:
            data, text = self._post_tc_status_payload(issue_key)
        except Exception:
            data, text = None, ""

        parsed = self._parse_tc_generation_status(data, text)
        if parsed.get("ok"):
            return parsed
        if int(parsed.get("failed_count", 0) or 0) > 0 or int(parsed.get("in_progress_count", 0) or 0) > 0:
            return parsed

        # API 응답이 비어 있거나 불완전한 경우 화면 HTML/생성된 AIO TC를 fallback으로 확인한다.
        try:
            html = self.get_text(f"/browse/{issue_key}")
        except Exception:
            html = ""
        html_parsed = self._parse_tc_generation_status(None, html)
        if html_parsed.get("ok"):
            return html_parsed
        if int(html_parsed.get("failed_count", 0) or 0) > 0 or int(html_parsed.get("in_progress_count", 0) or 0) > 0:
            return html_parsed

        # LLM History API가 빈 값이더라도 AIO TestCase가 실제 생성되어 있으면 생성 완료로 본다.
        # 사용자 화면의 AMAMRAPP-602처럼 History 완료 + AIO TC 존재인데 CheckTCStatus만 0으로 내려오는 케이스 방어.
        try:
            issue_id, project_id = self._get_issue_meta_for_aio(issue_key)
            actual, _ = self._get_aio_actual_count(issue_key, issue_id, project_id)
        except Exception:
            actual = None
        if actual is not None and int(actual) > 0:
            return {"complete_count": 1, "in_progress_count": 0, "failed_count": 0, "ok": True}

        return parsed

    def get_error_table_ok(self, issue_key: str, desc: str | None = None) -> bool:
        """연관 에러 표 검증.

        Jira 신 에디터는 description을 ADF(JSON)로 내려주는 경우가 많다.
        따라서 (1) ADF에서 '연관 에러' 섹션 다음 table을 찾아 검사하고,
        (2) ADF가 아니면(구 에디터/렌더링 HTML) HTML table 파싱으로 폴백한다.

        OK 조건(사용자 기준):
          - '연관 에러' 표가 존재하고,
          - 헤더(첫 행) 제외 데이터 행에 값이 1셀이라도 있으면 OK
        그 외(표 없음/데이터 없음/파싱 실패)는 FAIL.
        """
        if desc is None:
            data = self.get(
                f"/rest/api/2/issue/{issue_key}",
                params={"fields": "description"},
            )
            desc = (data.get("fields") or {}).get("description")

        # 1) ADF(JSON) 우선
        try:
            if isinstance(desc, dict):
                table = self._adf_find_table_after_heading(desc, ["연관 에러", "연관에러"])
                return self._adf_table_has_any_value(table)
        except Exception:
            return False

        # 2) HTML/Wiki 폴백
        try:
            if not isinstance(desc, str) or not desc.strip():
                return False

            extractor = _TableExtractor()
            extractor.feed(desc)

            error_keywords = {"에러명", "에러 조건"}
            for table in extractor.tables:
                if not table:
                    continue
                header_row = table[0]
                header_text = {c.strip().lower() for c in header_row}
                if not error_keywords.intersection(header_text):
                    continue
                for row in table[1:]:
                    if any(cell.strip() for cell in row):
                        return True
                return False

            return False
        except Exception:
            return False

    def get_design_rollout_ok(self, issue_key: str, desc: str | None = None) -> bool:
        """설계 횡전개 검증.

        OK 조건(사용자 기준):
          - '설계 횡전개 대상' 표가 존재하고,
          - 헤더 제외 데이터 행에 값이 1셀이라도 있으면 OK
        그 외(표 없음/데이터 없음/파싱 실패)는 FAIL.

        Jira 신 에디터 ADF(JSON) 우선 파싱, 아니면 HTML table 파싱으로 폴백.
        """
        if desc is None:
            data = self.get(
                f"/rest/api/2/issue/{issue_key}",
                params={"fields": "description"},
            )
            desc = (data.get("fields") or {}).get("description")

        # 1) ADF(JSON) 우선
        try:
            if isinstance(desc, dict):
                table = self._adf_find_table_after_heading(
                    desc,
                    ["설계 횡전개 대상", "설계횡전개 대상", "설계 횡전개", "설계횡전개"],
                )
                return self._adf_table_has_any_value(table)
        except Exception:
            return False

        # 2) HTML/Wiki 폴백
        try:
            if not isinstance(desc, str) or not desc.strip():
                return False

            extractor = _TableExtractor()
            extractor.feed(desc)

            rollout_keywords = {"설비 유형", "모델", "라인", "jira project"}
            for table in extractor.tables:
                if not table:
                    continue
                header_row = table[0]
                header_text = {c.strip().lower() for c in header_row}
                if not rollout_keywords.intersection(header_text):
                    continue
                for row in table[1:]:
                    if any(cell.strip() for cell in row):
                        return True
                return False

            return False
        except Exception:
            return False

    # -------------------------
    # ADF(JSON) helpers
    # -------------------------
    # -------------------------
    # Status helpers (tri-state)
    # -------------------------
    def get_error_table_status(self, issue_key: str, desc: str | None = None, debug: bool = False) -> str:
        """연관 에러 검증 상태 문자열을 반환한다.

        반환값: 'OK' | 'FAIL' | '사유 작성'
        - OK: 표 데이터 행에 값이 1셀이라도 있음
        - 사유 작성: 표는 비었지만, 표 전후/헤딩 라인에 사유 텍스트가 존재
        - FAIL: 표도 비었고 사유 텍스트도 없음
        """
        if debug:
            print(f"\n{'='*80}")
            print(f"[DEBUG STATUS] get_error_table_status 시작")
            print(f"[DEBUG STATUS] issue_key: {issue_key}")
            print(f"{'='*80}")
        
        if desc is None:
            data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "description"})
            desc = (data.get("fields") or {}).get("description")

        ok = self.get_error_table_ok(issue_key, desc=desc)
        if debug:
            print(f"[DEBUG STATUS] get_error_table_ok 결과: {ok}")
        
        if ok:
            if debug:
                print(f"[DEBUG STATUS] 표에 데이터 있음 → 'OK' 반환")
            return "OK"

        # 사유 텍스트 탐지
        if debug:
            print(f"[DEBUG STATUS] 표가 비어있음. 사유 텍스트 확인 중...")
        
        has_reason = self._has_reason_text_around_table(desc, heading_candidates=["연관 에러", "연관에러"], debug=debug)
        
        if debug:
            print(f"[DEBUG STATUS] _has_reason_text_around_table 결과: {has_reason}")
        
        if has_reason:
            if debug:
                print(f"[DEBUG STATUS] 최종 결과: '사유 작성'")
            return "사유 작성"
        
        if debug:
            print(f"[DEBUG STATUS] 최종 결과: 'FAIL'")
        return "FAIL"

    def get_design_rollout_status(self, issue_key: str, desc: str | None = None, debug: bool = False) -> str:
        """설계 횡전개 대상 검증 상태 문자열을 반환한다.

        반환값: 'OK' | 'FAIL' | '사유 작성'
        """
        if debug:
            print(f"\n{'='*80}")
            print(f"[DEBUG STATUS] get_design_rollout_status 시작")
            print(f"[DEBUG STATUS] issue_key: {issue_key}")
            print(f"{'='*80}")
        
        if desc is None:
            data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "description"})
            desc = (data.get("fields") or {}).get("description")

        ok = self.get_design_rollout_ok(issue_key, desc=desc)
        if debug:
            print(f"[DEBUG STATUS] get_design_rollout_ok 결과: {ok}")
        
        if ok:
            if debug:
                print(f"[DEBUG STATUS] 표에 데이터 있음 → 'OK' 반환")
            return "OK"

        if debug:
            print(f"[DEBUG STATUS] 표가 비어있음. 사유 텍스트 확인 중...")
        
        has_reason = self._has_reason_text_around_table(desc, heading_candidates=["설계 횡전개 대상", "설계횡전개대상"], debug=debug)
        
        if debug:
            print(f"[DEBUG STATUS] _has_reason_text_around_table 결과: {has_reason}")
        
        if has_reason:
            if debug:
                print(f"[DEBUG STATUS] 최종 결과: '사유 작성'")
            return "사유 작성"
        
        if debug:
            print(f"[DEBUG STATUS] 최종 결과: 'FAIL'")
        return "FAIL"

    def _has_reason_text_around_table(self, desc, heading_candidates: list[str], debug=False) -> bool:
        """'설명' 본문에서 특정 섹션(헤딩~다음 헤딩) 사이에 '사유 텍스트'가 존재하는지 검사한다.

        사용자 정의(최신):
        - '.연관 에러' 섹션: '.연관 에러' 헤딩을 찾고, 그 다음 '.설계 횡전개 대상' 헤딩 전까지의 영역에서
          (표 제외) 한글/영문/숫자가 포함된 텍스트가 있으면 '사유 작성'으로 본다.
        - '.설계 횡전개 대상' 섹션: 해당 헤딩 이후 본문 끝(또는 다음 큰 섹션 헤딩 전)까지 동일 규칙 적용.
        - 표(table) 내 텍스트(헤더 포함)는 사유 판단에서 제외한다.
        - 헤딩 라인 자체는 키워드(연관에러/설계횡전개대상)만 있는 경우 사유 아님.
          같은 라인에 키워드 외 문자가 더 있으면 사유.
        """
        
        if debug:
            print(f"\n{'='*80}")
            print(f"[DEBUG] _has_reason_text_around_table 시작")
            print(f"[DEBUG] heading_candidates: {heading_candidates}")
            print(f"[DEBUG] desc 타입: {type(desc)}")
            if isinstance(desc, dict):
                print(f"[DEBUG] desc는 ADF(JSON) 형식입니다")
            elif isinstance(desc, str):
                print(f"[DEBUG] desc는 문자열 형식입니다 (길이: {len(desc)})")
            print(f"{'='*80}\n")

        def _meaningful_text(s: str) -> bool:
            if not s:
                return False
            s2 = re.sub(r"<[^>]+>", " ", str(s))
            s2 = re.sub(r"\s+", " ", s2).strip()
            if not s2:
                return False
            return re.search(r"[0-9A-Za-z가-힣]", s2) is not None

        def _strip_bullets_prefix(s: str) -> str:
            # 앞쪽 불릿/점/기호 제거 (유니코드 불릿/점 포함)
            return re.sub(r"^[\s\-\–\—\*\·\.•●○◦▪■▶▷►]+", "", (s or "").strip())

        def _section_bounds_adf(blocks: list[dict], start_norms: list[str], end_norms: list[str] | None):
            start_i = None
            end_i = None
            # start
            for i, blk in enumerate(blocks):
                btype = (blk.get("type") or "").lower()
                if btype in ("paragraph", "heading"):
                    raw = (self._adf_collect_text(blk) or "").strip()
                    norm = self._adf_norm(_strip_bullets_prefix(raw))
                    if any(t in norm for t in start_norms):
                        start_i = i
                        break
            if start_i is None:
                return None, None
            if not end_norms:
                return start_i, len(blocks)
            for j in range(start_i + 1, len(blocks)):
                blk = blocks[j]
                btype = (blk.get("type") or "").lower()
                if btype in ("paragraph", "heading"):
                    raw = (self._adf_collect_text(blk) or "").strip()
                    norm = self._adf_norm(_strip_bullets_prefix(raw))
                    if any(t in norm for t in end_norms):
                        end_i = j
                        break
            return start_i, (end_i if end_i is not None else len(blocks))

        def _has_reason_in_adf_section(adf: dict, start_candidates: list[str], end_candidates: list[str] | None) -> bool:
            blocks = list(self._adf_iter_blocks(adf))
            start_norms = [self._adf_norm(x) for x in (start_candidates or []) if x]
            end_norms = [self._adf_norm(x) for x in (end_candidates or []) if x] if end_candidates else None
            
            if debug:
                print(f"[DEBUG ADF] 전체 블록 수: {len(blocks)}")
                print(f"[DEBUG ADF] start_norms: {start_norms}")
                print(f"[DEBUG ADF] end_norms: {end_norms}")
            
            if not start_norms:
                if debug:
                    print(f"[DEBUG ADF] start_norms가 비어있음 → False 반환")
                return False

            s, e = _section_bounds_adf(blocks, start_norms, end_norms)
            if s is None:
                if debug:
                    print(f"[DEBUG ADF] 시작 헤딩을 찾지 못함 → False 반환")
                return False
            
            if debug:
                print(f"[DEBUG ADF] 섹션 범위: 블록[{s}] ~ 블록[{e-1}]")

            # (1) 헤딩 라인: 키워드 제외 후 의미 텍스트가 있으면 사유
            head_raw = (self._adf_collect_text(blocks[s]) or "").strip()
            head_raw2 = _strip_bullets_prefix(head_raw)
            head_norm = self._adf_norm(head_raw2)
            
            if debug:
                print(f"\n[DEBUG ADF] (1) 헤딩 라인 검사:")
                print(f"  - head_raw: '{head_raw}'")
                print(f"  - head_raw2 (불릿 제거): '{head_raw2}'")
                print(f"  - head_norm (정규화): '{head_norm}'")
            
            # 키워드 제거
            rem = head_norm
            for t in start_norms:
                rem = rem.replace(t, "")
            
            if debug:
                print(f"  - rem (키워드 제거 후): '{rem}'")
            
            # 점(.) 및 기타 특수문자 제거 후 의미있는 텍스트만 검사
            rem = re.sub(r"[^\w가-힣]", "", rem)
            
            if debug:
                print(f"  - rem (특수문자 제거 후): '{rem}'")
            
            if re.search(r"[0-9a-z가-힣]", rem):
                if debug:
                    print(f"  → 헤딩에 의미있는 텍스트 발견! '사유 작성' 반환")
                return True
            
            if debug:
                print(f"  → 헤딩에는 의미있는 텍스트 없음")

            # (2) 섹션 범위 내(표 제외) 의미 텍스트
            if debug:
                print(f"\n[DEBUG ADF] (2) 섹션 범위 내 블록 검사:")
            
            for i in range(s + 1, e):
                blk = blocks[i]
                btype = (blk.get("type") or "").lower()
                
                if debug:
                    print(f"\n  블록[{i}] 타입: {btype}")
                
                if btype == "table":
                    if debug:
                        print(f"    → 표 블록이므로 건너뜀")
                    continue
                    
                if btype in ("paragraph", "heading"):
                    raw = (self._adf_collect_text(blk) or "").strip()
                    
                    if debug:
                        print(f"    raw: '{raw}'")
                    
                    if not raw:  # 빈 텍스트는 무시
                        if debug:
                            print(f"    → 빈 텍스트, 건너뜀")
                        continue
                        
                    raw2 = _strip_bullets_prefix(raw)
                    
                    if debug:
                        print(f"    raw2 (불릿 제거): '{raw2}'")
                    
                    if not raw2:  # 불릿 제거 후 빈 텍스트도 무시
                        if debug:
                            print(f"    → 불릿 제거 후 빈 텍스트, 건너뜀")
                        continue
                        
                    norm = self._adf_norm(raw2)
                    
                    if debug:
                        print(f"    norm (정규화): '{norm}'")
                    
                    # end 헤딩이 섞여 있으면 중단(안전)
                    if end_norms and any(t in norm for t in end_norms):
                        if debug:
                            print(f"    → end 헤딩 발견, 검사 중단")
                        break
                        
                    # start 헤딩 키워드만 반복된 경우는 제외
                    if any(t == norm for t in start_norms):
                        if debug:
                            print(f"    → start 헤딩 키워드 반복, 건너뜀")
                        continue
                        
                    # 실제 의미있는 텍스트인지 확인 (특수문자 제거 후)
                    cleaned = re.sub(r"[^\w가-힣]", "", raw2)
                    
                    if debug:
                        print(f"    cleaned (특수문자 제거): '{cleaned}'")
                    
                    if not cleaned:  # 특수문자만 있는 경우 무시
                        if debug:
                            print(f"    → 특수문자만 있음, 건너뜀")
                        continue
                        
                    if _meaningful_text(cleaned):
                        if debug:
                            print(f"    → 의미있는 텍스트 발견! '사유 작성' 반환")
                        return True
                    else:
                        if debug:
                            print(f"    → _meaningful_text 검사 실패")
            
            if debug:
                print(f"\n[DEBUG ADF] 모든 검사 완료 → False 반환")
            return False

        # 섹션 종류에 따라 end 후보를 결정
        # - 연관 에러 섹션: 설계 횡전개 대상 전까지
        # - 설계 횡전개 대상 섹션: 본문 끝까지
        heading_norms = [self._adf_norm(x) for x in (heading_candidates or []) if x]
        is_error_section = any("연관" in h for h in heading_norms)

        end_candidates = ["설계 횡전개 대상", "설계횡전개대상"] if is_error_section else None
        
        if debug:
            print(f"[DEBUG] is_error_section: {is_error_section}")
            print(f"[DEBUG] end_candidates: {end_candidates}")

        # 1) ADF(JSON)
        try:
            if isinstance(desc, dict):
                if debug:
                    print(f"\n[DEBUG] ADF(JSON) 파싱 시도")
                result = _has_reason_in_adf_section(desc, heading_candidates, end_candidates)
                if debug:
                    print(f"\n[DEBUG] ADF 파싱 결과: {result}")
                return result
        except Exception as ex:
            if debug:
                print(f"\n[DEBUG] ADF 파싱 중 예외 발생: {ex}")
            pass

        # 2) HTML / wiki string (보수적)
        try:
            if isinstance(desc, str) and desc.strip():
                if debug:
                    print(f"\n[DEBUG] HTML/Wiki 파싱 시도")
                
                plain = re.sub(r"<[^>]+>", " ", desc)
                plain = re.sub(r"\s+", " ", plain)
                
                if debug:
                    print(f"[DEBUG HTML] plain 텍스트 길이: {len(plain)}")
                    print(f"[DEBUG HTML] plain 미리보기 (처음 200자): {plain[:200]}")

                # start 위치
                start_pos = None
                for hc in heading_candidates or []:
                    p = plain.find(hc)
                    if p != -1 and (start_pos is None or p < start_pos):
                        start_pos = p
                        if debug:
                            print(f"[DEBUG HTML] '{hc}' 발견 위치: {p}")
                
                if start_pos is None:
                    if debug:
                        print(f"[DEBUG HTML] 시작 헤딩을 찾지 못함 → False 반환")
                    return False
                
                if debug:
                    print(f"[DEBUG HTML] 최종 start_pos: {start_pos}")

                end_pos = None
                if end_candidates:
                    for ec in end_candidates:
                        p = plain.find(ec, start_pos + 1)
                        if p != -1 and (end_pos is None or p < end_pos):
                            end_pos = p
                            if debug:
                                print(f"[DEBUG HTML] end_candidate '{ec}' 발견 위치: {p}")
                
                if debug:
                    print(f"[DEBUG HTML] 최종 end_pos: {end_pos}")

                section = plain[start_pos: (end_pos if end_pos is not None else len(plain))]
                
                if debug:
                    print(f"[DEBUG HTML] section 길이: {len(section)}")
                    print(f"[DEBUG HTML] section 내용:\n{section[:500]}")

                # table 텍스트 제거(대략)
                section = re.sub(r"<table[\s\S]*?</table>", " ", section, flags=re.IGNORECASE)
                
                if debug:
                    print(f"[DEBUG HTML] table 제거 후 section:\n{section[:500]}")

                # start 헤딩 제거
                for hc in heading_candidates or []:
                    section = section.replace(hc, " ")
                
                if debug:
                    print(f"[DEBUG HTML] 헤딩 제거 후 section:\n{section[:500]}")
                
                # 특수문자 제거 후 의미있는 텍스트 확인
                section_cleaned = re.sub(r"[^\w가-힣\s]", "", section)
                section_cleaned = re.sub(r"\s+", " ", section_cleaned).strip()
                
                if debug:
                    print(f"[DEBUG HTML] section_cleaned: '{section_cleaned}'")
                
                # 최소 2글자 이상의 한글/영문/숫자 연속이 있어야 사유로 인정
                match = re.search(r"[0-9A-Za-z가-힣]{2,}", section_cleaned)
                if match:
                    if debug:
                        print(f"[DEBUG HTML] 의미있는 텍스트 발견: '{match.group()}' → True 반환")
                    return True
                else:
                    if debug:
                        print(f"[DEBUG HTML] 의미있는 텍스트 없음 → False 반환")
                    return False
        except Exception as ex:
            if debug:
                print(f"\n[DEBUG HTML] 파싱 중 예외 발생: {ex}")
            return False

        if debug:
            print(f"\n[DEBUG] 모든 경로에서 False 반환")
        return False
    def _adf_norm(self, s: str) -> str:
        return (s or "").strip().lower().replace(" ", "")

    def _adf_collect_text(self, node) -> str:
        """ADF node에서 평문 텍스트를 추출."""
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "".join(self._adf_collect_text(n) for n in node)
        if not isinstance(node, dict):
            return ""
        t = node.get("type")
        if t == "text":
            return str(node.get("text") or "")
        return self._adf_collect_text(node.get("content"))

    def _adf_iter_blocks(self, adf: dict):
        content = (adf or {}).get("content") or []
        if isinstance(content, list):
            for n in content:
                if isinstance(n, dict):
                    yield n

    def _adf_find_table_after_heading(self, adf: dict, heading_candidates: list[str]):
        targets = {self._adf_norm(x) for x in (heading_candidates or []) if x}
        if not targets:
            return None

        seen = False
        for blk in self._adf_iter_blocks(adf):
            btype = blk.get("type")
            if btype in ("paragraph", "heading"):
                txt = self._adf_norm(self._adf_collect_text(blk))
                if any(t in txt for t in targets):
                    seen = True
                    continue
            if seen and btype == "table":
                return blk
        return None

    def _adf_table_has_any_value(self, table_node) -> bool:
        if not isinstance(table_node, dict) or table_node.get("type") != "table":
            return False
        rows = table_node.get("content") or []
        if not isinstance(rows, list) or len(rows) < 2:
            return False
        for row in rows[1:]:
            if not isinstance(row, dict) or row.get("type") != "tableRow":
                continue
            cells = row.get("content") or []
            if not isinstance(cells, list):
                continue
            for cell in cells:
                txt = self._adf_collect_text(cell)
                if str(txt).strip():
                    return True
        return False

    def get_link_validation(self, issue_key: str, links=None) -> dict:
        """이슈 링크 검증.

        확인된 Jira 화면 기준:
          - SW_VOC는 `is child of`뿐 아니라 `relates to`에도 연결될 수 있다.
          - 따라서 inwardIssue/outwardIssue 양쪽을 모두 보며, 상대 이슈의 issuetype/name 또는 key로 판별한다.
        """
        if links is None:
            data = self.get(
                f"/rest/api/2/issue/{issue_key}",
                params={"fields": "issuelinks"}
            )
            links = (data.get("fields") or {}).get("issuelinks") or []

        sw_voc_ok = False
        has_func_req = False
        has_detail_design = False

        def _norm(value) -> str:
            return str(value or "").strip().lower()

        def _linked_issue_info(issue: dict) -> tuple[str, str]:
            fields = issue.get("fields") or {}
            issue_type = ((fields.get("issuetype") or {}).get("name") or "")
            key = issue.get("key") or ""
            return _norm(issue_type), _norm(key)

        def _is_sw_voc(issue_type: str, key: str) -> bool:
            # 사내 SW_VOC 이슈는 화면/툴팁상 SW_VOC로 표시되고, 실제 key는 AMSWV-* 형태도 사용된다.
            if "sw_voc" in issue_type or "sw voc" in issue_type:
                return True
            if "voc" in issue_type and "sw" in issue_type:
                return True
            if key.startswith("amswv-") or key.startswith("swvoc-") or key.startswith("sw_voc-"):
                return True
            return False

        for link in links:
            # SW_VOC / Function Requirement / Detail Design 모두 link 방향과 link type에 의존하지 않고
            # 실제 연결된 상대 이슈의 타입/키를 기준으로 판단한다.
            for side_key in ("inwardIssue", "outwardIssue"):
                side = link.get(side_key)
                if not side:
                    continue

                issue_type, key = _linked_issue_info(side)

                if _is_sw_voc(issue_type, key):
                    sw_voc_ok = True
                if "function requirement" in issue_type or "function_requirement" in issue_type:
                    has_func_req = True
                if "detail design" in issue_type or "detail_design" in issue_type:
                    has_detail_design = True

        missing = []
        if not sw_voc_ok:
            missing.append("SW_VOC")
        if not has_func_req:
            missing.append("Function Requirement")
        if not has_detail_design:
            missing.append("Detail Design")

        return {"missing": missing, "sw_voc_ok": sw_voc_ok}

    def get_pr_merge_ok(self, issue_key: str, issue_id: str | None = None) -> bool:
        """병합된 PR이 1개 이상인지 확인.

        Jira dev-status API를 통해 stash(Bitbucket) 또는 GitHub PR 중
        status가 MERGED인 것이 있으면 True.
        """
        try:
            # 이슈 ID(숫자)
            if not issue_id:
                issue_data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "id"})
                issue_id = issue_data.get("id", "")
            if not issue_id:
                return False

            for app_type in ("stash", "github", "bitbucket"):
                try:
                    data = self.get(
                        "/rest/dev-status/latest/issue/detail",
                        params={
                            "issueId": issue_id,
                            "applicationType": app_type,
                            "dataType": "pullrequest",
                        }
                    )
                    for detail in (data.get("detail") or []):
                        for pr in (detail.get("pullRequests") or []):
                            if (pr.get("status") or "").upper() == "MERGED":
                                return True
                except Exception:
                    any_error = True
                    continue
            return False
        except Exception:
            return False

    def get_pr_merge_status(self, issue_key: str, issue_id: str | None = None) -> str:
        """PR 상태 요약 문자열을 반환한다.

        - dev-status API에서 pullRequests를 조회
        - 상태를 MERGED/OPEN/DECLINED/CLOSED 등으로 정규화
        - 여러 PR이 있으면 상태별 건수로 요약 (예: 'MERGED(1),OPEN(2)')
        - PR이 하나도 없으면 'NONE'
        - 조회/파싱 예외는 'ERR'
        """
        try:
            if not issue_id:
                issue_data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "id"})
                issue_id = issue_data.get("id", "")
            if not issue_id:
                return "ERR"

            counts: dict[str, int] = {}
            total_pr = 0
            any_success = False
            any_error = False

            def _norm_status(pr: dict) -> str:
                # 여러 스키마 호환
                raw = pr.get("status")
                if isinstance(raw, dict):
                    raw = raw.get("state") or raw.get("status") or raw.get("name") or ""
                if not raw:
                    raw = pr.get("state") or pr.get("status") or ""
                s = str(raw).upper()

                # merged 플래그 보정
                if pr.get("merged") is True or pr.get("isMerged") is True:
                    return "MERGED"

                if "MERGED" in s:
                    return "MERGED"
                if "OPEN" in s:
                    return "OPEN"
                if "DECLINED" in s or "DECLINE" in s:
                    return "DECLINED"
                if "CLOSED" in s or "CLOSE" in s:
                    return "CLOSED"
                return s or "UNKNOWN"

            for app_type in ("stash", "github", "bitbucket"):
                try:
                    data = self.get(
                        "/rest/dev-status/latest/issue/detail",
                        params={
                            "issueId": issue_id,
                            "applicationType": app_type,
                            "dataType": "pullrequest",
                        }
                    )
                    any_success = True
                    for detail in (data.get("detail") or []):
                        for pr in (detail.get("pullRequests") or []):
                            total_pr += 1
                            st = _norm_status(pr or {})
                            counts[st] = counts.get(st, 0) + 1
                except Exception:
                    any_error = True
                    continue

            if total_pr == 0:
                # dev-status 조회가 전부 실패한 경우(None으로 오판정 방지)
                if (not any_success) and any_error:
                    return "ERR"
                return "NONE"

            # 보기 좋게: MERGED, OPEN, DECLINED, CLOSED, UNKNOWN, 기타 순
            order = ["MERGED", "OPEN", "DECLINED", "CLOSED", "UNKNOWN"]
            parts = []
            for k in order:
                if k in counts:
                    parts.append(f"{k}({counts[k]})")
            for k in sorted(counts.keys()):
                if k not in order:
                    parts.append(f"{k}({counts[k]})")
            return ",".join(parts)
        except Exception:
            return "ERR"

    def get_sccb_target_checks(self, issue_key: str) -> dict:
        """병렬 처리 + 공통 데이터 1회 조회로 검증 속도 개선."""
        import concurrent.futures

        results = {
            "body_len": "",
            "rollout_ok": False,
            "err_table_ok": True,
            "missing_links": ["Detail Design", "Function Requirement", "SW_VOC"],
            "tc_complete": 0,
            "tc_ok": False,
            "pr_merge_ok": False,
        }

        # 공통 데이터 1회 조회 (description/issuelinks/id)
        try:
            core = self.get_issue_core(issue_key)
        except Exception:
            core = {}

        fields = (core.get("fields") or {})
        desc = fields.get("description") or ""
        issuelinks = fields.get("issuelinks") or []
        issue_id = core.get("id") or ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {}

            futures["body_len"] = executor.submit(self.get_body_length_string_from_ui, issue_key)
            futures["err_table"] = executor.submit(self.get_error_table_ok, issue_key, desc)
            futures["rollout"] = executor.submit(self.get_design_rollout_ok, issue_key, desc)
            futures["link"] = executor.submit(self.get_link_validation, issue_key, issuelinks)
            futures["tc"] = executor.submit(self.get_tc_generation_check, issue_key)
            futures["pr_merge"] = executor.submit(self.get_pr_merge_ok, issue_key, issue_id)

            for key, future in futures.items():
                try:
                    result = future.result(timeout=10)
                    if key == "body_len":
                        results["body_len"] = result
                    elif key == "err_table":
                        results["err_table_ok"] = result
                    elif key == "rollout":
                        results["rollout_ok"] = result
                    elif key == "link":
                        results["missing_links"] = (result.get("missing", []) if isinstance(result, dict) else [])
                    elif key == "tc":
                        results["tc_complete"] = (result.get("complete_count", 0) if isinstance(result, dict) else 0)
                        results["tc_ok"] = bool((result or {}).get("ok", False)) if isinstance(result, dict) else False
                    elif key == "pr_merge":
                        results["pr_merge_ok"] = bool(result)
                except Exception:
                    pass

        return results

    def get_issue_core(self, issue_key: str) -> dict:
        """SCCB 대상 검증에서 공통으로 쓰는 필드(id/description/issuelinks)를 1회에 가져온다."""
        return self.get(
            f"/rest/api/2/issue/{issue_key}",
            params={"fields": "description,issuelinks"}
        )

    def get_issues_core_batch(self, issue_keys: list[str]) -> dict[str, dict]:
        """여러 이슈의 core 데이터를 배치로 조회 (성능 최적화용)
        
        Returns:
            {issue_key: core_data, ...}
        """
        if not issue_keys:
            return {}
        
        jql = f"key in ({','.join(issue_keys)})"
        try:
            data = self.get(
                "/rest/api/2/search",
                params={
                    "jql": jql,
                    "fields": "description,issuelinks",
                    "maxResults": len(issue_keys)
                }
            )
            issues = data.get("issues", []) or []
            return {issue["key"]: issue for issue in issues}
        except Exception:
            return {}

    def list_fields(self):
        if self._fields_cache is None:
            self._fields_cache = self.get("/rest/api/2/field") or []
        return self._fields_cache

    @staticmethod
    def _norm_field_name(s: str) -> str:
        return (s or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")

    def find_field_id(self, field_name: str) -> str | None:
        key = self._norm_field_name(field_name)
        if not key:
            return None
        if key in self._field_id_cache:
            return self._field_id_cache[key]

        for f in self.list_fields():
            if self._norm_field_name(f.get("name")) == key:
                fid = f.get("id")
                if fid:
                    self._field_id_cache[key] = fid
                    return fid
        self._field_id_cache[key] = None
        return None

    def search(self, jql: str, max_results: int = 50, extra_fields: list[str] | None = None):
        """JQL 검색.

        Jira search는 페이징(startAt)을 지원하므로,
        UI의 max_results 만큼은 '누락 없이' 가져오도록 안전하게 페이징한다.
        """
        fields = ["summary", "status", "assignee", "duedate"]
        if extra_fields:
            for ef in extra_fields:
                if ef and ef not in fields:
                    fields.append(ef)

        want = max(0, int(max_results or 0))
        page_size = min(50, want) if want else 50

        all_issues = []
        start_at = 0
        total = None

        while True:
            data = self.get(
                "/rest/api/2/search",
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": page_size,
                    "fields": ",".join(fields),
                },
            )
            if total is None:
                total = data.get("total")
            issues = data.get("issues", []) or []
            if not issues:
                break
            all_issues.extend(issues)
            if want and len(all_issues) >= want:
                all_issues = all_issues[:want]
                break
            start_at += len(issues)
            if total is not None and start_at >= int(total):
                break

        return {
            "issues": all_issues,
            "total": total if total is not None else len(all_issues),
            "maxResults": want,
        }

    @staticmethod
    def _extract_confluence_page_id(page_url: str) -> str:
        url = (page_url or "").strip()
        if not url:
            return ""
        m = re.search(r"/pages/(\d+)(?:/|$)", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]pageId=(\d+)(?:&|$)", url)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _extract_confluence_base_url(page_url: str) -> str:
        m = re.match(r"^(https?://[^/]+)", (page_url or "").strip())
        return m.group(1) if m else ""

    def _get_json_absolute(self, url: str, params=None):
        r = self._session().get(
            url,
            params=params,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.json()

    @staticmethod
    def _extract_keys_from_confluence_body(body_text: str) -> set[str]:
        text = body_text or ""
        if not text:
            return set()

        marker_variants = [
            "사전SCCB 검토 의견",
            "사전 SCCB 검토 의견",
        ]
        key_pat = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
        chunks = []
        for marker in marker_variants:
            pos = text.find(marker)
            if pos >= 0:
                chunks.append(text[pos:pos + 160000])
        chunks.append(text)

        for chunk in chunks:
            m = re.search(r"<table\b[\s\S]*?</table>", chunk, re.IGNORECASE)
            target = m.group(0) if m else chunk
            keys = {k.upper() for k in key_pat.findall(target)}
            if keys:
                return keys
        return set()

    def _get_confluence_page_body(self, page_url: str) -> dict:
        page_id = self._extract_confluence_page_id(page_url)
        if not page_id:
            raise ValueError("Confluence page id를 URL에서 찾지 못했습니다.")

        conf_base = self._extract_confluence_base_url(page_url)
        if not conf_base:
            raise ValueError("Confluence base url을 URL에서 찾지 못했습니다.")

        api_url = f"{conf_base}/rest/api/content/{page_id}"
        return self._get_json_absolute(
            api_url,
            params={"expand": "body.storage,body.view,title"},
        ) or {}

    def get_weekly_sccb_issue_keys(self, page_url: str) -> set[str]:
        """Confluence API로 주간 SCCB 페이지의 '사전SCCB 검토 의견' 영역에서 이슈 키를 추출한다."""
        url = (page_url or "").strip()
        if not url:
            return set()

        data = self._get_confluence_page_body(url)
        body = data.get("body") or {}

        storage_text = (((body.get("storage") or {}).get("value")) or "")
        keys = self._extract_keys_from_confluence_body(storage_text)
        if keys:
            return keys

        view_text = (((body.get("view") or {}).get("value")) or "")
        keys = self._extract_keys_from_confluence_body(view_text)
        if keys:
            return keys

        return set()

    def get_linked_sw_voc_keys(self, issue_key: str) -> list[str]:
        data = self.get(
            f"/rest/api/2/issue/{issue_key}",
            params={"fields": "issuelinks"}
        )
        links = (data.get("fields") or {}).get("issuelinks") or []

        def _norm(value) -> str:
            return str(value or "").strip().lower()

        def _is_sw_voc(issue: dict) -> bool:
            fields = issue.get("fields") or {}
            issue_type = _norm((fields.get("issuetype") or {}).get("name"))
            key = _norm(issue.get("key"))
            if "sw_voc" in issue_type or "sw voc" in issue_type:
                return True
            if "voc" in issue_type and "sw" in issue_type:
                return True
            if key.startswith("amswv-") or key.startswith("swvoc-") or key.startswith("sw_voc-"):
                return True
            return False

        results = []
        seen = set()
        for link in links:
            for side_key in ("inwardIssue", "outwardIssue"):
                side = link.get(side_key)
                if not side:
                    continue
                side_key_val = (side.get("key") or "").strip().upper()
                if not side_key_val or side_key_val in seen:
                    continue
                if _is_sw_voc(side):
                    seen.add(side_key_val)
                    results.append(side_key_val)
        return results

    def myself(self):
        return self.get("/rest/api/2/myself")

    def get_issue_status(self, issue_key: str) -> str:
        data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "status"})
        return (((data.get("fields") or {}).get("status") or {}).get("name") or "").strip()

    def get_issue_field(self, issue_key: str, field_id: str):
        data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": field_id})
        return (data.get("fields") or {}).get(field_id)

    def get_transitions(self, issue_key: str):
        data = self.get(f"/rest/api/2/issue/{issue_key}/transitions", params={"expand": "transitions.fields"})
        return data.get("transitions", []) or []

    def do_transition(self, issue_key: str, payload: dict):
        self.post(f"/rest/api/2/issue/{issue_key}/transitions", payload)
