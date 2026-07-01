import requests
import threading
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
import re
import html as _html
import json
import time
from html.parser import HTMLParser
from urllib.parse import urljoin
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


class _ConfluenceCopyFormParser(HTMLParser):
    """Confluence의 페이지 복사 화면에서 저장용 form 값만 추출한다.

    Confluence Server/Data Center의 페이지 복사는 공개 REST API가 아니라 화면 action을
    통해 수행된다. 서버가 렌더링한 form의 hidden token과 현재 버전별 필수 필드를 그대로
    제출하기 위해, 특정 버전의 필드명에 의존하지 않고 form을 읽는다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self._form = None
        self._textarea_name = None
        self._textarea_chunks = []
        self._select_name = None
        self._select_options = []

    @staticmethod
    def _attrs(attrs):
        return {str(k).lower(): "" if v is None else str(v) for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "form":
            self._form = {
                "action": a.get("action", ""),
                "method": a.get("method", "get").lower(),
                "fields": [],
                "checkboxes": [],
            }
            return
        if self._form is None:
            return

        if tag == "input":
            name = a.get("name", "")
            input_type = a.get("type", "text").lower()
            if not name or "disabled" in a:
                return
            # checkbox/radio는 선택된 값만 전송된다. 복사 화면의 첨부파일
            # 선택지는 이후 강제로 포함할 수 있도록 메타데이터도 보관한다.
            if input_type in {"checkbox", "radio"}:
                self._form["checkboxes"].append({
                    "name": name,
                    "value": a.get("value", "true"),
                    "checked": "checked" in a,
                })
                if "checked" not in a:
                    return
            if input_type in {"submit", "button", "reset", "image", "file"}:
                return
            self._form["fields"].append((name, a.get("value", "")))
        elif tag == "textarea":
            self._textarea_name = a.get("name", "") if "disabled" not in a else ""
            self._textarea_chunks = []
        elif tag == "select":
            self._select_name = a.get("name", "") if "disabled" not in a else ""
            self._select_options = []
        elif tag == "option" and self._select_name:
            self._select_options.append({
                "value": a.get("value", ""),
                "selected": "selected" in a,
                "text": [],
            })

    def handle_data(self, data):
        if self._textarea_name:
            self._textarea_chunks.append(data)
        elif self._select_name and self._select_options:
            self._select_options[-1]["text"].append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "textarea" and self._form is not None:
            if self._textarea_name:
                self._form["fields"].append((self._textarea_name, "".join(self._textarea_chunks)))
            self._textarea_name = None
            self._textarea_chunks = []
        elif tag == "select" and self._form is not None:
            if self._select_name and self._select_options:
                selected = next((x for x in self._select_options if x["selected"]), self._select_options[0])
                value = selected["value"] or "".join(selected["text"])
                self._form["fields"].append((self._select_name, value))
            self._select_name = None
            self._select_options = []
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None

    def find_copy_form(self):
        for form in self.forms:
            action = (form.get("action") or "").lower()
            if "docopypage.action" in action:
                return form
        return None


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
            from urllib3.util.retry import Retry
            s = requests.Session()
            s.auth = self._auth()
            s.verify = self.verify_ssl
            retry = Retry(
                total=3,
                backoff_factor=0.5,          # 0.5s, 1s, 2s 간격 재시도
                status_forcelist={500, 502, 503, 504},
                allowed_methods={"GET"},
                raise_on_status=False,
            )
            adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=retry)
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
        if m and int(m.group(1)) >= 10:
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
                if m and int(m.group(1)) >= 10:
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
        # 비정상 범위(너무 작거나 너무 큰 값)는 오탐으로 판단
        if len(plain) < 10 or len(plain) > 50000:
            return ""
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
        m = re.search(r'\b([ABCD])\b', s)
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
        """AIO traceability 후보 endpoint를 모두 조회한다.

        기존 구현의 문제:
        - 첫 번째 non-empty 응답만 반환했다.
        - requirement 응답이 cycle=0 정보만 가지고 있고 TC 목록은 associations 쪽에 있는 경우,
          첫 응답에서 멈추므로 실제 Test Case 6개를 끝까지 보지 못했다.

        반환:
        - [(label, payload), ...]
        - 상위 로직은 각 payload에서 TC 개수를 추출한 뒤 최대값을 채택한다.
        """
        issue_key = str(issue_key or "").strip()
        issue_id = str(issue_id or "").strip()
        paths = []
        seen = set()

        def add(label, path, params=None):
            key = (path, tuple(sorted((params or {}).items())))
            if key in seen:
                return
            seen.add(key)
            paths.append((label, path, params or None))

        if project_id is not None:
            base = f"/rest/aio-tcms/1.0/project/{int(project_id)}"
            now_ms = int(_time.time() * 1000)
            if issue_id:
                # F12 기준 실제 TC 매핑 우선 후보
                add("requirement", f"{base}/traceability/requirement/{issue_id}", {
                    "showAllCaseUserVersions": "false", "c_pId": int(project_id), "t": now_ms,
                })
                add("associations", f"{base}/traceability/associations/{issue_id}", {
                    "c_pId": int(project_id), "t": now_ms,
                })
                # 일부 AIO 버전은 issueId/taskId 명칭이 다르다.
                add("issue_id", f"{base}/traceability/issue/{issue_id}", {"c_pId": int(project_id), "t": now_ms})
                add("jiraIssue", f"{base}/traceability/jiraIssue/{issue_id}", {"c_pId": int(project_id), "t": now_ms})
                add("jiraissue", f"{base}/traceability/jiraissue/{issue_id}", {"c_pId": int(project_id), "t": now_ms})
                add("task_id", f"{base}/traceability/task/{issue_id}", {"c_pId": int(project_id), "t": now_ms})
                add("traceability_issueId_param", f"{base}/traceability", {"issueId": issue_id, "c_pId": int(project_id), "t": now_ms})
                add("traceability_taskId_param", f"{base}/traceability", {"taskId": issue_id, "c_pId": int(project_id), "t": now_ms})

            add("issue_key", f"{base}/traceability/issue/{issue_key}", {"c_pId": int(project_id), "t": now_ms})
            add("traceability_issueKey_param", f"{base}/traceability", {"issueKey": issue_key, "c_pId": int(project_id), "t": now_ms})

        payloads = []
        for label, path, params in paths:
            try:
                data = self.get(path, params=params)
            except Exception:
                continue
            # 빈 dict/list는 후보로 의미가 거의 없다. 단, 0 자체는 payload가 아니므로 제외.
            if data:
                payloads.append((label, data))
        return payloads

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
        """AIO payload에서 실제 Test Case 개수를 추출한다.

        핵심 원칙:
        - 검증 기준은 Test Cycle 개수가 아니라 Test Case 개수다.
        - cycle=0이어도 Test Case가 존재하면 OK/FAIL 판단에 사용해야 한다.
        - endpoint마다 같은 TC 수가 반복될 수 있으므로 합산하지 않고 후보 중 최대값을 쓴다.
        """
        if not data:
            return {}

        tc_keys: set[str] = set()
        tc_array_max = 0
        explicit_tc_total_max = 0
        cycle_total_max = 0

        def norm_key(k) -> str:
            return re.sub(r"[^a-z0-9]", "", str(k or "").lower())

        def to_int(v) -> int:
            if isinstance(v, bool):
                return 0
            try:
                n = int(v)
                return n if n > 0 else 0
            except Exception:
                return 0

        def looks_tc_key_text(text: str) -> bool:
            return bool(re.search(r"[A-Z][A-Z0-9_]+-TC-\d+", text or ""))

        def collect_tc_keys(obj):
            if isinstance(obj, str):
                for m in re.finditer(r"[A-Z][A-Z0-9_]+-TC-\d+", obj):
                    tc_keys.add(m.group(0))
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    collect_tc_keys(str(k))
                    collect_tc_keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    collect_tc_keys(v)

        def is_tc_container_key(k: str) -> bool:
            nk = norm_key(k)
            return (
                "testcase" in nk
                or nk in {"tests", "cases", "associatedcases", "linkedcases", "coveredcases", "requirementscases"}
                or nk.endswith("cases")
            )

        def is_cycle_key(k: str) -> bool:
            nk = norm_key(k)
            return nk in {"testcycle", "cycle", "cycles", "testrun", "testruns", "execution", "executions"}

        def looks_like_tc_row(row) -> bool:
            if isinstance(row, str):
                return looks_tc_key_text(row)
            if not isinstance(row, dict):
                return False
            if JiraClient._looks_like_aio_testcase_row(row):
                return True
            keys = {norm_key(k) for k in row.keys()}
            joined = " ".join(JiraClient._iter_text_values(row))
            if looks_tc_key_text(joined):
                return True
            # AIO 응답에서 title 없이 id/key/version만 내려오는 경우 방어
            has_id = bool(keys & {"id", "key", "testcaseid", "testcasekey", "tcid", "caseid"})
            has_case_marker = any("testcase" in k for k in keys) or bool(keys & {"objective", "precondition", "testdata", "steps", "script"})
            return has_id and has_case_marker

        def count_tc_list(lst) -> int:
            if not isinstance(lst, list) or not lst:
                return 0
            # 문자열 TC 키 배열
            str_hits = sum(1 for x in lst if isinstance(x, str) and looks_tc_key_text(x))
            if str_hits > 0:
                return str_hits
            dict_items = [x for x in lst if isinstance(x, dict)]
            if not dict_items:
                return 0
            sample = dict_items[:50]
            hits = sum(1 for x in sample if looks_like_tc_row(x))
            # parent key가 testcase 계열이 아니어도 내용상 TC row면 채택
            if hits >= max(1, (len(sample) + 1) // 2):
                return len(dict_items)
            return 0

        def extract_total_from_tc_dict(d: dict) -> int:
            """testCases: {total: 6, values:[...]} 같은 구조 처리."""
            if not isinstance(d, dict):
                return 0
            best = 0
            for name in ("total", "count", "totalCount", "size", "length", "numberOfElements", "totalElements",
                         "totalTests", "testCaseCount", "totalTestCases", "testCasesCount"):
                best = max(best, to_int(d.get(name)))
            for name in ("items", "values", "results", "data", "rows", "content", "list", "testCases", "cases"):
                v = d.get(name)
                if isinstance(v, list):
                    best = max(best, len(v) if v else 0, count_tc_list(v))
            return best

        def walk(obj, parent_key: str = "", in_cycle: bool = False):
            nonlocal tc_array_max, explicit_tc_total_max, cycle_total_max

            if isinstance(obj, list):
                if is_tc_container_key(parent_key):
                    # testCases: [...] 는 내용 판별이 약해도 TC 목록으로 본다.
                    tc_array_max = max(tc_array_max, len([x for x in obj if x is not None]))
                tc_array_max = max(tc_array_max, count_tc_list(obj))
                for item in obj:
                    walk(item, parent_key, in_cycle or is_cycle_key(parent_key))
                return

            if not isinstance(obj, dict):
                return

            parent_is_tc = is_tc_container_key(parent_key)
            parent_is_cycle = in_cycle or is_cycle_key(parent_key)

            # dict 자체가 testCases 컨테이너일 때 total/results 처리
            if parent_is_tc:
                explicit_tc_total_max = max(explicit_tc_total_max, extract_total_from_tc_dict(obj))

            for k, v in obj.items():
                nk = norm_key(k)
                key_is_tc = is_tc_container_key(k)
                key_is_cycle = is_cycle_key(k)

                # testCases/testCaseCount 계열은 cycle 여부와 무관하게 TC 후보다.
                if key_is_tc:
                    if isinstance(v, list):
                        tc_array_max = max(tc_array_max, len([x for x in v if x is not None]), count_tc_list(v))
                    elif isinstance(v, dict):
                        explicit_tc_total_max = max(explicit_tc_total_max, extract_total_from_tc_dict(v))
                    else:
                        explicit_tc_total_max = max(explicit_tc_total_max, to_int(v))

                # 명시적 TC 카운터만 TC 총계로 사용한다.
                if nk in {"testcasecount", "testcasescount", "totaltestcases", "totaltestcase", "casecount", "totalcases"}:
                    explicit_tc_total_max = max(explicit_tc_total_max, to_int(v))

                # progress.total / summary.total 은 testCases 컨테이너 내부일 때만 TC 총계로 본다.
                if parent_is_tc and nk in {"total", "count", "totalcount", "totaltests", "size", "length", "totalelements"}:
                    explicit_tc_total_max = max(explicit_tc_total_max, to_int(v))

                # cycle/testRun 내부 totalTests는 fallback 전용이다.
                if parent_is_cycle and nk in {"totaltests", "totaltest", "total", "count", "totalcount", "testcasecount"}:
                    cycle_total_max = max(cycle_total_max, to_int(v))

                if isinstance(v, dict):
                    # progress/summary/statistics가 testCases 컨테이너 안에 있을 수 있음
                    child_parent = k
                    walk(v, child_parent, parent_is_cycle or key_is_cycle)
                elif isinstance(v, list):
                    walk(v, k, parent_is_cycle or key_is_cycle)

        collect_tc_keys(data)
        walk(data)

        candidates = {
            "tc_keys": len(tc_keys),
            "tc_array": tc_array_max,
            "explicit_total": explicit_tc_total_max,
            "cycle_total": cycle_total_max,
        }
        # 양수인 후보를 모두 반환한다.
        # 상위 _get_aio_actual_count 가 add_count()로 최대값을 채택하므로
        # 여기서 best 1개만 내보낼 필요가 없다.
        result = {label: int(val) for label, val in candidates.items() if int(val or 0) > 0}
        if not result:
            return {}
        return result

    def _get_aio_actual_count(self, issue_key: str, issue_id: str | None = None, project_id: int | None = None) -> tuple[int | None, dict[str, int]]:
        """AIO 테스트 케이스 실제 개수 조회.

        endpoint별 결과를 합산하지 않고 최대값을 채택한다.
        동일 TC가 requirement/associations/html에 반복 노출될 수 있기 때문이다.
        """
        counts: dict[str, int] = {}
        best = 0

        def add_count(label: str, value: int):
            nonlocal best
            try:
                n = int(value)
            except Exception:
                return
            if n <= 0:
                return
            counts[label] = max(counts.get(label, 0), n)
            if n > best:
                best = n

        if project_id is not None:
            try:
                payloads = self._get_aio_traceability_issue_json_candidates(issue_key, issue_id, project_id)
            except Exception:
                payloads = []
            for label, payload in payloads:
                extracted = self._extract_aio_cycle_totals_from_payload(payload)
                for k, v in extracted.items():
                    add_count(f"api:{label}:{k}", v)

        # API에서 못 잡거나 required보다 낮으면 browse HTML fallback도 시도한다.
        # best <= 0: API에서 아무것도 못 잡은 경우
        # best < min_required: API가 뭔가를 잡았지만 약한 값(cycle_total 등)일 수 있으므로 재확인
        min_required = min(self.DIFFICULTY_MIN_CASES.values())  # 최솟값 = D난이도 기준 (2)
        html = ""
        if best < min_required:
            try:
                html = self.get_text(f"/browse/{issue_key}")
            except Exception:
                html = ""

            html_count = self._extract_aio_count_from_html(html)
            if html_count:
                add_count("html", html_count)

            refs = self._extract_aio_task_refs_from_html(html)
            for project_id2, task_id in refs:
                try:
                    payload = self._get_aio_traceability_task_json(project_id2, task_id)
                except Exception:
                    continue
                for k, v in self._extract_aio_cycle_totals_from_payload(payload).items():
                    add_count(f"task:{task_id}:{k}", v)

        if best <= 0:
            return None, {}
        return int(best), counts

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

                # 표(table) 내용을 '먼저' 제거해야 한다.
                # 주의: HTML 태그를 먼저 통째로 지우면 <table> 태그도 함께 사라져
                #       표 헤더/셀 텍스트(예: '에러명', '설비 유형')가 본문 텍스트로 남아
                #       빈 표인데도 '사유 작성'으로 오판정된다. (버그)
                #   (a) HTML 표: <table>...</table> 블록 제거
                #   (b) Jira wiki markup 표: '|' 로 시작하는 표 행(||헤더||, |셀|) 라인 제거
                no_table = re.sub(r"<table[\s\S]*?</table>", "\n", desc, flags=re.IGNORECASE)
                no_table = "\n".join(
                    line for line in no_table.splitlines()
                    if not re.match(r"^\s*\|", line)
                )

                plain = re.sub(r"<[^>]+>", " ", no_table)
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

                # 표 텍스트는 위에서 이미 제거됨(HTML <table> 블록 및 wiki 표 행).
                # 혹시 남아있을 수 있는 인라인 <table> 잔여물만 방어적으로 한 번 더 제거.
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

    @staticmethod
    def _extract_pull_requests_from_devstatus_payload(data) -> list[dict]:
        """dev-status detail/summary 변형에서 pull request 객체를 재귀적으로 수집한다."""
        out: list[dict] = []
        seen = set()

        def looks_like_pr(d: dict) -> bool:
            if not isinstance(d, dict):
                return False
            keys = {str(k).lower() for k in d.keys()}
            # pullRequests 컨테이너 자체는 PR 객체가 아니다
            if "pullrequest" in " ".join(keys) or "pullrequests" in " ".join(keys):
                return False
            # PR로 인정하려면 상태 필드 + PR임을 확인할 수 있는 단서가 함께 있어야 한다.
            has_status = bool(keys & {"status", "state", "merged", "ismerged"})
            has_pr_url = "pull" in str(d.get("url") or "").lower() or "pull" in str(d.get("href") or "").lower()
            has_pr_type = "pull" in str(d.get("type") or "").lower()
            # pullRequestId / pullrequest 관련 키가 있으면 명확한 PR
            has_pr_key = bool(keys & {"pullrequestid", "prid", "pr_id", "pullrequest_id"})
            # 브랜치/커밋 dict는 PR이 아님: branch/commit/ref 전용 키만 있는 경우 제외
            only_branch_keys = keys <= {"name", "url", "href", "id", "type", "state", "status",
                                        "createdate", "updatedate", "lastupdated", "branch",
                                        "repository", "ref", "refname", "displayid"}
            if only_branch_keys and not has_pr_url and not has_pr_type and not has_pr_key:
                return False
            return has_status and (has_pr_url or has_pr_type or has_pr_key)

        def add_pr(pr):
            if not isinstance(pr, dict):
                return
            pid = pr.get("id") or pr.get("pullRequestId") or pr.get("url") or pr.get("name") or pr.get("title") or id(pr)
            key = str(pid)
            if key in seen:
                return
            seen.add(key)
            out.append(pr)

        def walk(obj, parent_key: str = ""):
            if isinstance(obj, list):
                if str(parent_key).lower() in {"pullrequests", "pullrequest", "prs", "pullrequestdata"}:
                    for item in obj:
                        add_pr(item)
                for item in obj:
                    walk(item, parent_key)
                return
            if not isinstance(obj, dict):
                return

            for k, v in obj.items():
                kl = str(k).lower()
                if kl in {"pullrequests", "pullrequest", "prs", "pullrequestdata",
                          "mergerequests", "mergerequest", "mrs"}:
                    if isinstance(v, list):
                        for item in v:
                            add_pr(item)
                    elif isinstance(v, dict):
                        # 단일 PR 객체 또는 values/items 내부 배열
                        if looks_like_pr(v):
                            add_pr(v)
                        for child_key in ("values", "items", "data", "results", "content"):
                            child = v.get(child_key)
                            if isinstance(child, list):
                                for item in child:
                                    add_pr(item)
                elif isinstance(v, dict) and looks_like_pr(v) and kl in {"pullrequest", "pr",
                                                                           "mergerequest", "mr"}:
                    add_pr(v)
                walk(v, k)

        walk(data)
        return out

    @staticmethod
    def _normalize_pr_status(pr: dict) -> str:
        if not isinstance(pr, dict):
            return "UNKNOWN"
        if pr.get("merged") is True or pr.get("isMerged") is True:
            return "MERGED"
        raw = pr.get("status")
        if isinstance(raw, dict):
            raw = raw.get("state") or raw.get("status") or raw.get("name") or raw.get("value") or ""
        if not raw:
            raw = pr.get("state") or pr.get("reviewStatus") or pr.get("mergeStatus") or ""
        s = str(raw or "").upper()
        if "MERGED" in s or s == "MERGE":
            return "MERGED"
        if "OPEN" in s:
            return "OPEN"
        if "DECLINED" in s or "DECLINE" in s:
            return "DECLINED"
        if "CLOSED" in s or "CLOSE" in s:
            return "CLOSED"
        if "UNKNOWN" in s:
            return "UNKNOWN"
        return s or "UNKNOWN"

    def _get_devstatus_app_types(self, issue_id: str) -> tuple[list[str], int, bool]:
        """dev-status summary에서 PR applicationType 후보와 overall count를 얻는다."""
        app_types: list[str] = []
        overall_count = 0
        had_error = False

        def add_app_type(v):
            """원본 그대로 + 소문자 변형도 추가 (Jira는 applicationType 대소문자 구분)"""
            if not v:
                return
            s = str(v).strip()
            if not s:
                return
            existing_lower = [x.lower() for x in app_types]
            if s not in app_types:
                app_types.append(s)
            if s.lower() not in app_types and s.lower() not in existing_lower:
                app_types.append(s.lower())

        try:
            summary = self.get(
                "/rest/dev-status/latest/issue/summary",
                params={"issueId": issue_id},
            ) or {}
            sm = summary.get("summary") if isinstance(summary.get("summary"), dict) else {}
            pr_sum = sm.get("pullrequest") if isinstance(sm.get("pullrequest"), dict) else {}
            overall = pr_sum.get("overall") if isinstance(pr_sum.get("overall"), dict) else {}
            try:
                overall_count = int(overall.get("count") or 0)
            except Exception:
                overall_count = 0

            by_inst = pr_sum.get("byInstanceType") if isinstance(pr_sum.get("byInstanceType"), dict) else {}
            for inst_key, inst_val in by_inst.items():
                cnt = 0
                try:
                    cnt = int((inst_val or {}).get("count") or 0)
                except Exception:
                    cnt = 0
                if cnt > 0:
                    add_app_type(inst_key)
                    if isinstance(inst_val, dict):
                        add_app_type(inst_val.get("applicationType"))
                        add_app_type(inst_val.get("type"))
                        add_app_type(inst_val.get("name"))
        except Exception:
            had_error = True

        # 사내 Bitbucket Server 계열까지 기본 후보에 포함
        for at in ("bitbucketserver", "stash", "bitbucket", "github", "gitlab", "fecru"):
            add_app_type(at)
        return app_types, overall_count, had_error

    def get_pr_merge_ok(self, issue_key: str, issue_id: str | None = None) -> bool:
        """병합된 PR이 1개 이상인지 확인."""
        status = self.get_pr_merge_status(issue_key, issue_id)
        return "MERGED" in str(status or "").upper()

    def get_pr_merge_status(self, issue_key: str, issue_id: str | None = None) -> str:
        """PR 상태 요약 문자열을 반환한다.

        dev-status detail 응답 내부 pullRequests 위치가 repository/branch/detail 하위로 달라질 수 있어
        재귀적으로 PR 객체를 수집한다.
        """
        try:
            if not issue_id:
                try:
                    issue_data = self.get(f"/rest/api/2/issue/{issue_key}", params={"fields": "id"})
                    issue_id = issue_data.get("id", "")
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code in (401, 403):
                        return "N/A(권한없음)"
                    raise
            if not issue_id:
                return "ERR"

            app_types_to_try, summary_pr_overall, summary_error = self._get_devstatus_app_types(str(issue_id))
            counts: dict[str, int] = {}
            total_pr = 0
            any_success = False
            any_no_permission = False
            any_error = bool(summary_error)
            seen_pr = set()

            for app_type in app_types_to_try:
                try:
                    data = self.get(
                        "/rest/dev-status/latest/issue/detail",
                        params={
                            "issueId": issue_id,
                            "applicationType": app_type,
                            "dataType": "pullrequest",
                        },
                    ) or {}
                    any_success = True
                    prs = self._extract_pull_requests_from_devstatus_payload(data)
                    for pr in prs:
                        pid = pr.get("id") or pr.get("pullRequestId") or pr.get("url") or pr.get("name") or pr.get("title") or str(pr)
                        pid = str(pid)
                        if pid in seen_pr:
                            continue
                        seen_pr.add(pid)
                        total_pr += 1
                        st = self._normalize_pr_status(pr)
                        counts[st] = counts.get(st, 0) + 1
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code in (401, 403):
                        any_no_permission = True
                    else:
                        any_error = True
                    continue
                except Exception:
                    any_error = True
                    continue

            # 권한 없음: 성공한 호출이 없고 403/401만 받은 경우
            if any_no_permission and not any_success and total_pr == 0:
                return "N/A(권한없음)"

            if total_pr == 0 and summary_pr_overall > 0:
                # summary상 PR은 있는데 detail 구조/권한/instance 문제로 상태를 못 읽은 경우.
                counts["UNKNOWN"] = summary_pr_overall
                total_pr = summary_pr_overall

            if total_pr == 0:
                if (not any_success) and any_error:
                    return "ERR"
                return "NONE"

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}

            futures["body_len"] = executor.submit(self.get_body_length_string_from_ui, issue_key)
            futures["err_table"] = executor.submit(self.get_error_table_ok, issue_key, desc)
            futures["rollout"] = executor.submit(self.get_design_rollout_ok, issue_key, desc)
            futures["link"] = executor.submit(self.get_link_validation, issue_key, issuelinks)
            futures["tc"] = executor.submit(self.get_tc_generation_check, issue_key)
            futures["pr_merge"] = executor.submit(self.get_pr_merge_ok, issue_key, issue_id)

            # AIO/PR은 여러 endpoint를 순회하므로 timeout을 넉넉하게 준다.
            SLOW_KEYS = {"tc", "pr_merge"}
            for key, future in futures.items():
                t = 45 if key in SLOW_KEYS else 15
                try:
                    result = future.result(timeout=t)
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
            params={"fields": "id,description,issuelinks"}
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
                    "fields": "id,description,issuelinks",
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

    def _post_json_absolute(self, url: str, json_body: dict):
        r = self._session().post(
            url,
            json=json_body,
            timeout=self.timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.json() if r.text else None

    def _put_json_absolute(self, url: str, json_body: dict):
        r = self._session().put(
            url,
            json=json_body,
            timeout=self.timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r.json() if r.text else None

    def _get_html_absolute(self, url: str, params=None):
        r = self._session().get(
            url,
            params=params,
            timeout=self.timeout,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r

    def _post_form_absolute(self, url: str, form_fields, referer: str = ""):
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if referer:
            headers["Referer"] = referer
        r = self._session().post(
            url,
            data=form_fields,
            timeout=self.timeout,
            headers=headers,
            allow_redirects=True,
        )
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {url}\n{r.text}", response=r)
        return r

    @staticmethod
    def _replace_form_field(fields, name: str, value: str):
        """HTML form의 특정 필드는 기존 값을 제거한 뒤 지정 값 하나만 넣는다."""
        target = (name or "").strip()
        if not target:
            return list(fields or [])
        result = [(k, v) for k, v in (fields or []) if k != target]
        result.append((target, value))
        return result

    @staticmethod
    def _parse_weekly_confluence_title(title: str, reference_date=None) -> dict:
        """
        주간 SCCB 제목의 주차/날짜 범위를 해석한다.

        예) ``26W (06/22~06/29)``
        - 연도는 제목에 없으므로, 기준일과 가장 가까운 실제 날짜 범위를 선택한다.
        - 이 방식으로 12월~1월 경계에서 다음 해 1월 페이지를 만들 때도 연도를 올바르게 처리한다.
        """
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        src = (title or "").strip()
        pat = re.compile(
            r"(?P<week>\d{1,2})W\s*\(\s*"
            r"(?P<sm>\d{1,2})/(?P<sd>\d{1,2})\s*~\s*"
            r"(?P<em>\d{1,2})/(?P<ed>\d{1,2})\s*\)"
        )
        m = pat.search(src)
        if not m:
            raise ValueError(f"페이지 제목에서 주차 패턴을 찾지 못했습니다: {title}")

        week = int(m.group("week"))
        sm = int(m.group("sm"))
        sd = int(m.group("sd"))
        em = int(m.group("em"))
        ed = int(m.group("ed"))
        today = reference_date or datetime.now(ZoneInfo("Asia/Seoul")).date()

        # 제목에는 연도가 없으므로, 전년/당년/다음년 후보를 비교한다.
        # 우선순위는 제목의 W 번호와 실제 ISO 주차가 일치하고 시작일이 월요일인 후보이다.
        # 이 기준이 있어야 53W (12/28~01/03)처럼 연말 제목을 몇 달 전에 미리
        # 생성해도 직전 연도로 오인하지 않는다. 그 다음으로 오늘과의 거리를 사용한다.
        candidates = []
        for start_year in (today.year - 1, today.year, today.year + 1):
            try:
                start = date(start_year, sm, sd)
                end_year = start_year + 1 if (em, ed) < (sm, sd) else start_year
                end = date(end_year, em, ed)
            except ValueError:
                continue
            if today < start:
                distance = (start - today).days
            elif today > end:
                distance = (today - end).days
            else:
                distance = 0

            iso_week_matches = start.isocalendar().week == week
            starts_on_monday = start.isoweekday() == 1
            candidate_priority = 0 if (iso_week_matches and starts_on_monday) else 1
            candidates.append((candidate_priority, distance, start, end))

        if not candidates:
            raise ValueError(f"페이지 제목의 날짜가 올바르지 않습니다: {title}")

        _, _, start, end = min(candidates, key=lambda item: (item[0], item[1]))
        return {
            "match": m,
            "week": week,
            "start": start,
            "end": end,
        }

    @classmethod
    def _make_next_week_title(cls, title: str, reference_date=None) -> tuple[str, dict]:
        """
        예) 25W (06/15~06/21) -> 26W (06/22~06/28)

        SCCB 주간 페이지 기간은 **월요일~일요일(7일)** 기준이다.
        원본 제목의 종료일이 과거 관행처럼 다음 주 월요일로 적혀 있더라도,
        새로 생성하는 제목/본문 기간은 다음 시작일 기준의 일요일로 정규화한다.

        다음 주차는 단순 ``+1`` 대신 다음 시작일의 ISO 주차를 사용한다.
        따라서 연말에는 52W/53W에서 1W로 자연스럽게 전환된다.
        """
        from datetime import timedelta

        src = title or ""
        info = cls._parse_weekly_confluence_title(src, reference_date=reference_date)
        old_start = info["start"]
        next_start = old_start + timedelta(days=7)
        # SCCB 페이지는 월~일 범위로 표시한다. (다음 월요일이 아님)
        next_end = next_start + timedelta(days=6)
        next_week = next_start.isocalendar().week
        replacement = (
            f"{next_week}W ({next_start.month:02d}/{next_start.day:02d}"
            f"~{next_end.month:02d}/{next_end.day:02d})"
        )
        m = info["match"]
        return src[:m.start()] + replacement + src[m.end():], {
            **info,
            "next_start": next_start,
            "next_end": next_end,
            "next_week": next_week,
        }

    @staticmethod
    def _replace_weekly_date_ranges_in_body(
        body: str,
        old_title: str,
        new_title: str,
        old_start,
        old_end,
        new_start,
        new_end,
    ) -> str:
        """주간 페이지 머리말에 있는 날짜 범위를 다음 주 범위로 갱신한다.

        실제 Confluence 본문에서 자주 쓰는 날짜 표기 3가지를 지원한다.
        - ``2026. 6. 22. ~ 2026. 6. 29.``
        - ``2026-06-22 ~ 2026-06-29``
        - ``06/22~06/29`` 또는 ``6/22 ~ 6/29``

        원본 주간 범위와 정확히 일치하는 구간만 바꾸므로, 이슈 생성일/희망일 같은
        표 내부의 다른 날짜를 넓게 치환하지 않는다.
        """
        text = body or ""
        if old_title and new_title:
            text = text.replace(old_title, new_title)

        old_dot = (
            rf"{old_start.year}\s*\.\s*0?{old_start.month}\s*\.\s*0?{old_start.day}\s*\.?"
            rf"\s*~\s*"
            rf"{old_end.year}\s*\.\s*0?{old_end.month}\s*\.\s*0?{old_end.day}\s*\.?"
        )
        new_dot = (
            f"{new_start.year}. {new_start.month}. {new_start.day}. "
            f"~ {new_end.year}. {new_end.month}. {new_end.day}."
        )
        text = re.sub(old_dot, new_dot, text)

        old_dash = (
            rf"{old_start.year}-0?{old_start.month}-0?{old_start.day}"
            rf"\s*~\s*"
            rf"{old_end.year}-0?{old_end.month}-0?{old_end.day}"
        )
        new_dash = f"{new_start:%Y-%m-%d} ~ {new_end:%Y-%m-%d}"
        text = re.sub(old_dash, new_dash, text)

        old_slash = (
            rf"(?<!\d)0?{old_start.month}\s*/\s*0?{old_start.day}"
            rf"\s*~\s*"
            rf"0?{old_end.month}\s*/\s*0?{old_end.day}(?!\d)"
        )
        new_slash = f"{new_start:%m/%d}~{new_end:%m/%d}"
        text = re.sub(old_slash, new_slash, text)

        # SCCB 본문 하단의 ``사전SCCB 검토 의견`` 제목에는 Stiltsoft Handy
        # Date 매크로 2개가 나란히 배치되어 있다. 화면에서는 아래처럼 렌더링된다.
        #
        #   <time datetime="2026-06-22" class="... handy-date-time">
        #       <span class="handy-date-value">2026. 6. 22.</span>
        #   </time>
        #
        # 날짜가 하나씩 독립된 인라인 매크로라서 위의 ``범위`` 치환만으로는
        # 바뀌지 않는다. 이 부분은 Handy Date 요소/매크로 내부에서만 날짜를
        # 바꿔, 다른 표의 개별 이슈 일정은 건드리지 않는다.
        #
        # 주간 이동에서는 보통 ``새 시작일 == 기존 종료일``이므로, 날짜를 순차
        # 치환하면 시작일이 두 번 바뀌는 문제가 생긴다. 따라서 두 날짜를 하나의
        # 정규식/콜백으로 매핑한다.
        date_pairs = (
            (old_start, new_start),
            (old_end, new_end),
        )
        iso_replacements = {
            old_date.strftime("%Y-%m-%d"): new_date.strftime("%Y-%m-%d")
            for old_date, new_date in date_pairs
        }
        display_patterns = [
            (
                rf"{old_date.year}\s*\.\s*0?{old_date.month}\s*\.\s*"
                rf"0?{old_date.day}\s*\.?",
                f"{new_date.year}. {new_date.month}. {new_date.day}.",
            )
            for old_date, new_date in date_pairs
        ]
        display_pattern = re.compile(
            "|".join(f"(?P<d{i}>{pattern})" for i, (pattern, _) in enumerate(display_patterns))
        )

        def replace_display_date(match):
            for index, (_, new_display) in enumerate(display_patterns):
                if match.group(f"d{index}") is not None:
                    return new_display
            return match.group(0)

        iso_pattern = re.compile(
            "|".join(re.escape(old_iso) for old_iso in iso_replacements),
            re.IGNORECASE,
        )

        def replace_iso_date(match):
            return iso_replacements.get(match.group(0), match.group(0))

        # Confluence 화면 HTML 형태. ``handy-date-time`` class가 있는
        # time 태그만 대상으로 하므로, 일반 본문의 같은 날짜는 바꾸지 않는다.
        time_pattern = re.compile(
            r"<time\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\bhandy-date-time\b[^'\"]*['\"])"
            r"(?P<attrs>[^>]*)>(?P<body>.*?)</time>",
            re.IGNORECASE | re.DOTALL,
        )

        datetime_pattern = re.compile(
            r"(?P<prefix>\bdatetime\s*=\s*['\"])(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>['\"])",
            re.IGNORECASE,
        )

        def replace_handy_time(match):
            def replace_datetime(match_datetime):
                old_iso = match_datetime.group("date")
                return (
                    f"{match_datetime.group('prefix')}"
                    f"{iso_replacements.get(old_iso, old_iso)}"
                    f"{match_datetime.group('suffix')}"
                )

            attrs = datetime_pattern.sub(replace_datetime, match.group("attrs"))
            body_text = display_pattern.sub(replace_display_date, match.group("body"))
            return f"<time{attrs}>{body_text}</time>"

        text = time_pattern.sub(replace_handy_time, text)

        # Storage XML 형태도 함께 처리한다. 실제 저장 포맷은 Confluence/플러그인
        # 버전에 따라 다를 수 있으므로, ac:name에 date가 들어간 매크로 내부에서만
        # ISO 날짜와 표시 날짜를 갱신한다.
        date_macro_pattern = re.compile(
            r"<ac:structured-macro\b(?=[^>]*\bac:name\s*=\s*['\"][^'\"]*date[^'\"]*['\"])"
            r"[^>]*>.*?</ac:structured-macro>",
            re.IGNORECASE | re.DOTALL,
        )

        def replace_handy_storage_macro(match):
            macro = iso_pattern.sub(replace_iso_date, match.group(0))
            return display_pattern.sub(replace_display_date, macro)

        text = date_macro_pattern.sub(replace_handy_storage_macro, text)

        return text

    @staticmethod
    def _get_page_parent_id(page: dict) -> str:
        ancestors = (page or {}).get("ancestors") or []
        if not ancestors:
            return ""
        return str((ancestors[-1] or {}).get("id") or "").strip()

    @staticmethod
    def _get_page_grandparent_id(page: dict) -> str:
        """페이지의 부모가 놓인 상위 레벨 ID를 반환한다.

        주간 페이지의 직접 부모는 ``26년 사전 SCCB 기록`` 같은 연도별
        기록 페이지이고, 이 값은 해당 연도별 기록 페이지를 새로 만들 위치다.
        직접 부모가 Space 최상위에 있으면 빈 문자열을 반환한다.
        """
        ancestors = (page or {}).get("ancestors") or []
        if len(ancestors) < 2:
            return ""
        return str((ancestors[-2] or {}).get("id") or "").strip()

    @staticmethod
    def _make_yearly_sccb_parent_title(year: int) -> str:
        """예: 2027 -> ``27년 사전 SCCB 기록``"""
        return f"{int(year) % 100:02d}년 사전 SCCB 기록"

    def _find_existing_confluence_sibling_page(
        self,
        conf_base: str,
        space_key: str,
        title: str,
        parent_id: str,
    ) -> dict | None:
        """동일 Space 안에서 같은 부모를 가진 동일 제목 페이지가 있는지 확인한다."""
        existing = self._get_json_absolute(
            f"{conf_base}/rest/api/content",
            params={
                "spaceKey": space_key,
                "title": title,
                "type": "page",
                "expand": "_links,ancestors",
            },
        ) or {}
        for page in existing.get("results") or []:
            if self._get_page_parent_id(page) == parent_id:
                return page
        return None

    @staticmethod
    def _confluence_page_url(conf_base: str, page: dict) -> str:
        """Confluence content 응답의 webui 경로를 절대 URL로 변환한다."""
        webui = ((page or {}).get("_links") or {}).get("webui") or ""
        return f"{conf_base}{webui}" if webui else ""

    def _copy_confluence_single_page(
        self,
        conf_base: str,
        source_page_id: str,
        target_parent_id: str,
        target_parent_title: str,
        space_key: str,
        new_title: str,
    ) -> dict:
        """Confluence Server/DC의 기본 ``페이지 복사`` 화면을 통해 원본을 복제한다.

        사내 Confluence처럼 Server/Data Center 환경에서는 단일 페이지 복사 공개 REST API가
        제공되지 않는 경우가 많다. 따라서 Confluence가 실제 화면에서 사용하는
        ``copypage.action → docopypage.action`` 흐름을 그대로 사용한다. 서버가 렌더링한
        form(토큰 포함)을 읽어 다시 제출하므로, 원본 페이지의 표/레이아웃/매크로와
        첨부파일은 Confluence 자체 복사 로직으로 유지된다.
        """
        copy_form_url = f"{conf_base}/pages/copypage.action"
        opening = self._get_html_absolute(
            copy_form_url,
            params={
                "idOfPageToCopy": str(source_page_id),
                "idOfPageToCopyTo": str(target_parent_id or ""),
                "spaceKey": space_key,
            },
        )
        parser = _ConfluenceCopyFormParser()
        parser.feed(opening.text or "")
        parser.close()
        form = parser.find_copy_form()
        if not form:
            raise ValueError(
                "Confluence 페이지 복사 화면에서 저장 form을 찾지 못했습니다. "
                "복사 권한 또는 Confluence 화면 구성을 확인하세요."
            )

        fields = list(form.get("fields") or [])
        # 화면에 첨부파일 포함 옵션이 있으면 자동으로 체크한다. 이미지/파일을 참조하는
        # 매크로가 원본과 동일하게 렌더링되도록 하기 위함이다.
        for checkbox in form.get("checkboxes") or []:
            checkbox_name = str(checkbox.get("name") or "")
            normalized_name = checkbox_name.lower()
            if checkbox_name and ("attachment" in normalized_name or "file" in normalized_name):
                fields = self._replace_form_field(
                    fields,
                    checkbox_name,
                    str(checkbox.get("value") or "true"),
                )

        fields = self._replace_form_field(fields, "title", new_title)
        fields = self._replace_form_field(fields, "spaceKey", space_key)
        # Confluence 버전에 따라 parentPageId/parentPageString 둘 중 하나만 쓰기도 하므로
        # 둘 다 맞춰 준다. 일반 주차는 source form이 이미 동일 부모를 가리키며, 연도 전환은
        # 새로운 연도별 상위 페이지로 명시적으로 바뀐다.
        if target_parent_id:
            fields = self._replace_form_field(fields, "parentPageId", str(target_parent_id))
            if target_parent_title:
                fields = self._replace_form_field(fields, "parentPageString", target_parent_title)
        fields = self._replace_form_field(fields, "idOfPageToCopy", str(source_page_id))
        fields = self._replace_form_field(fields, "idOfPageToCopyTo", str(target_parent_id or ""))

        action = (form.get("action") or "").strip()
        if not action:
            action = f"docopypage.action?idOfPageToCopy={source_page_id}"
        submit_url = urljoin(f"{conf_base}/pages/", action)
        saved = self._post_form_absolute(submit_url, fields, referer=opening.url)

        # Confluence는 저장 후 페이지 보기 화면으로 redirect하는 것이 정상이다. URL에서 ID를
        # 우선 얻고, 버전별 redirect 차이는 같은 부모/제목의 페이지 검색으로 보완한다.
        candidates = [saved.url or "", saved.headers.get("Location", "") or ""]
        for candidate in candidates:
            copied_id = self._extract_confluence_page_id(candidate)
            if not copied_id:
                m = re.search(r"[?&]pageId=(\d+)(?:&|$)", candidate)
                copied_id = m.group(1) if m else ""
            if copied_id:
                return {
                    "id": copied_id,
                    "_links": {"webui": candidate[len(conf_base):] if candidate.startswith(conf_base) else ""},
                }

        # Redirect URL이 pageId를 노출하지 않는 설치 환경도 있으므로 제목/부모로 재확인한다.
        for delay in (0.0, 0.3, 0.7, 1.2):
            if delay:
                time.sleep(delay)
            copied = self._find_existing_confluence_sibling_page(
                conf_base=conf_base,
                space_key=space_key,
                title=new_title,
                parent_id=target_parent_id,
            )
            if copied:
                return copied

        page_preview = re.sub(r"\s+", " ", (saved.text or ""))[:400]
        raise RuntimeError(
            "Confluence 페이지 복사 요청 후 생성된 페이지를 확인하지 못했습니다. "
            f"응답 URL: {saved.url} / 응답 일부: {page_preview}"
        )

    def _update_copied_page_week_range(
        self,
        conf_base: str,
        copied_page_id: str,
        new_title: str,
        old_title: str,
        week_info: dict,
        target_parent_id: str,
    ) -> dict:
        """복사 완료 후 제목/주간 기간 텍스트만 최소 변경한다.

        Confluence가 복사한 페이지의 storage 본문을 다시 읽고 날짜 관련 문자열만 치환한다.
        원본 storage의 표/매크로/스타일 구조 전체를 유지하기 위한 처리다.
        """
        copied = self._get_json_absolute(
            f"{conf_base}/rest/api/content/{copied_page_id}",
            params={"expand": "body.storage,version,space,ancestors,_links"},
        ) or {}

        version_number = int(((copied.get("version") or {}).get("number") or 0))
        if version_number <= 0:
            raise ValueError("복사된 Confluence 페이지의 version 정보를 찾지 못했습니다.")

        storage = (copied.get("body") or {}).get("storage") or {}
        original_body = storage.get("value") or ""
        updated_body = self._replace_weekly_date_ranges_in_body(
            body=original_body,
            old_title=old_title,
            new_title=new_title,
            old_start=week_info["start"],
            old_end=week_info["end"],
            new_start=week_info["next_start"],
            new_end=week_info["next_end"],
        )
        representation = storage.get("representation") or "storage"

        payload = {
            "id": str(copied_page_id),
            "type": "page",
            "title": new_title,
            "version": {"number": version_number + 1},
            "body": {
                "storage": {
                    "value": updated_body,
                    "representation": representation,
                }
            },
        }
        # 복사 API의 목적지 아래에 유지되도록 부모를 명시한다.
        if target_parent_id:
            payload["ancestors"] = [{"id": str(target_parent_id)}]

        return self._put_json_absolute(
            f"{conf_base}/rest/api/content/{copied_page_id}",
            payload,
        ) or {}

    def _clone_confluence_page_storage(
        self,
        conf_base: str,
        source_page_id: str,
        target_parent_id: str,
        space_key: str,
        old_title: str,
        new_title: str,
        week_info: dict,
    ) -> dict:
        """원본 주간 페이지의 Confluence storage 본문을 다음 주 페이지로 복제한다.

        실제 SEMES Confluence 9.2 복사 대화상자는 ``form action="#"``와
        ``#copy-dialog-next`` 버튼으로 구성된 클라이언트 측 흐름이다. 따라서 예전처럼
        ``docopypage.action`` form을 찾는 방식은 이 설치 환경에서 동작하지 않는다.

        SCCB 주간 페이지에는 첨부파일이 없다는 전제에서, 원본의 storage format을 그대로
        읽어 새 페이지 생성 요청에 넣는다. Storage format은 표/셀 병합/색상/매크로/
        페이지 레이아웃 등 화면 포맷을 담고 있으므로, 본문을 재구성하지 않고 원본을
        복제한 뒤 제목과 주간 날짜 범위만 최소 치환한다.
        """
        source = self._get_json_absolute(
            f"{conf_base}/rest/api/content/{source_page_id}",
            params={"expand": "body.storage,space,ancestors,_links"},
        ) or {}

        storage = (source.get("body") or {}).get("storage") or {}
        source_body = storage.get("value")
        if source_body is None:
            raise ValueError(
                "원본 Confluence 페이지의 storage 본문을 읽지 못했습니다. "
                "페이지 조회 권한과 Confluence REST API 권한을 확인하세요."
            )

        cloned_body = self._replace_weekly_date_ranges_in_body(
            body=source_body,
            old_title=old_title,
            new_title=new_title,
            old_start=week_info["start"],
            old_end=week_info["end"],
            new_start=week_info["next_start"],
            new_end=week_info["next_end"],
        )
        representation = storage.get("representation") or "storage"

        payload = {
            "type": "page",
            "title": new_title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": cloned_body,
                    "representation": representation,
                }
            },
        }
        if target_parent_id:
            payload["ancestors"] = [{"id": str(target_parent_id)}]

        created = self._post_json_absolute(f"{conf_base}/rest/api/content", payload) or {}
        created_id = str(created.get("id") or "").strip()
        if not created_id:
            raise ValueError("Confluence 주간 페이지 생성 후 page id를 받지 못했습니다.")
        return created


    @staticmethod
    def _parse_meeting_minutes_confluence_title(
        title: str,
        parent_title: str = "",
        reference_date=None,
    ) -> dict:
        """SCCB 회의록 제목의 주차와 회의일을 해석한다.

        예) ``25W (06/22일) - 5건(A0, B3, C2)``

        회의록의 주차 번호는 SCCB 주간 페이지의 ISO 주차와 한 칸 차이가 날 수 있어,
        날짜를 ISO 주차로 재계산하지 않는다. 제목에 적힌 주차 번호는 그대로 다음 번호로
        넘기고, 회의일만 7일 이동한다. 연도는 우선 ``26년 회의록 (SCCB)`` 같은
        직접 상위 페이지 제목에서 얻는다.
        """
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        src = (title or "").strip()
        pattern = re.compile(
            r"(?P<week>\d{1,2})W\s*\(\s*"
            r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s*일\s*\)"
        )
        match = pattern.search(src)
        if not match:
            raise ValueError(f"회의록 페이지 제목에서 주차/날짜 패턴을 찾지 못했습니다: {title}")

        week = int(match.group("week"))
        month = int(match.group("month"))
        day = int(match.group("day"))

        parent_match = re.search(
            r"(?P<year>\d{2,4})년\s*회의록\s*\(\s*SCCB\s*\)",
            parent_title or "",
            re.IGNORECASE,
        )
        if parent_match:
            year = int(parent_match.group("year"))
            if year < 100:
                year += 2000
            try:
                meeting_date = date(year, month, day)
            except ValueError as exc:
                raise ValueError(f"회의록 제목의 날짜가 올바르지 않습니다: {title}") from exc
        else:
            today = reference_date or datetime.now(ZoneInfo("Asia/Seoul")).date()
            candidates = []
            for year in (today.year - 1, today.year, today.year + 1):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    continue
                candidates.append((abs((candidate - today).days), candidate))
            if not candidates:
                raise ValueError(f"회의록 제목의 날짜가 올바르지 않습니다: {title}")
            meeting_date = min(candidates, key=lambda item: item[0])[1]

        return {
            "match": match,
            "week": week,
            "date": meeting_date,
        }

    @classmethod
    def _make_next_meeting_minutes_title(
        cls,
        title: str,
        parent_title: str = "",
        reference_date=None,
    ) -> tuple[str, dict]:
        """회의록 제목을 다음 주 회의일 기준으로 만든다.

        예) ``25W (06/22일) - 5건(A0, B3, C2)``
        -> ``26W (06/29일) - 5건(A0, B3, C2)``.

        뒤의 건수/등급 문자열은 복제 시점의 기존 회의록 정보를 보존한다.
        SCCB 리스트 매크로는 별도로 다음 주 완료일 범위로 바뀌므로, 실제 이슈 수는
        새 페이지에서 Confluence/Jira가 다시 렌더링한 값으로 확인한다.
        """
        from datetime import timedelta

        src = title or ""
        info = cls._parse_meeting_minutes_confluence_title(
            src,
            parent_title=parent_title,
            reference_date=reference_date,
        )
        next_date = info["date"] + timedelta(days=7)

        # 회의록 관행의 주차 번호를 유지한다. 새해 첫 회의는 1W로 재시작한다.
        if next_date.year != info["date"].year:
            next_week = 1
        else:
            next_week = info["week"] + 1
            if next_week > 53:
                next_week = 1

        replacement = f"{next_week}W ({next_date:%m/%d}일)"
        match = info["match"]
        return src[:match.start()] + replacement + src[match.end():], {
            **info,
            "next_date": next_date,
            "next_week": next_week,
        }

    @staticmethod
    def _make_yearly_meeting_minutes_parent_title(year: int) -> str:
        """예: 2027 -> ``27년 회의록 (SCCB)``."""
        return f"{int(year) % 100:02d}년 회의록 (SCCB)"

    @staticmethod
    def _replace_meeting_minutes_body(
        body: str,
        old_title: str,
        new_title: str,
        old_meeting_date,
        new_meeting_date,
    ) -> str:
        """복제한 SCCB 회의록 본문에서 다음 주에 필요한 값만 바꾼다.

        - ``날짜/시간`` 표의 Handy Date / 날짜 표시
        - ``3. SCCB 리스트`` 아래 Jira 매크로의 ``SCCB 완료일`` 조건 날짜

        일반 표의 과거 회의 내용과 이슈별 날짜는 건드리지 않는다.
        """
        from datetime import datetime, timedelta

        text = body or ""
        if old_title and new_title:
            text = text.replace(old_title, new_title)

        old_iso = old_meeting_date.strftime("%Y-%m-%d")
        new_iso = new_meeting_date.strftime("%Y-%m-%d")
        delta_days = (new_meeting_date - old_meeting_date).days

        old_display_pattern = re.compile(
            rf"{old_meeting_date.year}\s*\.\s*0?{old_meeting_date.month}\s*\.\s*"
            rf"0?{old_meeting_date.day}\s*\.?"
        )
        new_display = f"{new_meeting_date.year}. {new_meeting_date.month}. {new_meeting_date.day}."

        def replace_single_date(fragment: str) -> str:
            fragment = re.sub(
                rf"(?<!\d){re.escape(old_iso)}(?!\d)",
                new_iso,
                fragment,
            )
            return old_display_pattern.sub(new_display, fragment)

        # Confluence view 형태: ``날짜/시간`` 행의 Handy Date time 태그.
        # 해당 행 안에서만 날짜를 바꿔서 회의 본문/이슈 표의 개별 날짜는 유지한다.
        date_row_pattern = re.compile(
            r"<tr\b[^>]*>(?:(?!</tr>).)*?날짜\s*/\s*시간(?:(?!</tr>).)*?</tr>",
            re.IGNORECASE | re.DOTALL,
        )
        text = date_row_pattern.sub(
            lambda match: replace_single_date(match.group(0)),
            text,
        )

        # Storage XML에서는 플러그인 버전에 따라 date/handy-date macro로 저장된다.
        # 매크로 본문에 기존 회의일이 실제로 있는 경우에만 갱신한다.
        date_macro_pattern = re.compile(
            r"<ac:structured-macro\b(?=[^>]*\bac:name\s*=\s*['\"][^'\"]*date[^'\"]*['\"])"
            r"[^>]*>.*?</ac:structured-macro>",
            re.IGNORECASE | re.DOTALL,
        )

        def replace_date_macro(match):
            macro = match.group(0)
            if old_iso not in macro and not old_display_pattern.search(macro):
                return macro
            return replace_single_date(macro)

        text = date_macro_pattern.sub(replace_date_macro, text)

        # ``3. SCCB 리스트``의 Jira 매크로는 JQL과 RAW 파라미터에 같은 조건을
        # 두 번 보관한다. SCCB 완료일 조건 바로 뒤에 있는 날짜만 7일 이동하므로,
        # created/개발 DR 완료일 등 고정 기준 날짜에는 영향이 없다.
        jira_macro_pattern = re.compile(
            r"<ac:structured-macro\b(?=[^>]*\bac:name\s*=\s*['\"]jira['\"])"
            r"[^>]*>.*?</ac:structured-macro>",
            re.IGNORECASE | re.DOTALL,
        )
        completion_condition_pattern = re.compile(
            r"(?P<prefix>"
            r"(?:&quot;|\"|')?SCCB\s*완료일(?:&quot;|\"|')?\s*"
            r"(?:&gt;=|&lt;=|&amp;gt;=|&amp;lt;=|>=|<=)\s*"
            r")"
            r"(?P<date>\d{4}-\d{1,2}-\d{1,2})",
            re.IGNORECASE,
        )

        def replace_completion_date(match):
            raw_date = match.group("date")
            try:
                parsed = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                return match.group(0)
            shifted = parsed + timedelta(days=delta_days)
            return f"{match.group('prefix')}{shifted:%Y-%m-%d}"

        def replace_jira_macro(match):
            macro = match.group(0)
            if "SCCB 완료일" not in macro:
                return macro
            return completion_condition_pattern.sub(replace_completion_date, macro)

        text = jira_macro_pattern.sub(replace_jira_macro, text)
        return text

    def _clone_confluence_meeting_minutes_storage(
        self,
        conf_base: str,
        source_page_id: str,
        target_parent_id: str,
        space_key: str,
        old_title: str,
        new_title: str,
        meeting_info: dict,
    ) -> dict:
        """원본 SCCB 회의록 Storage를 복제해 다음 주 회의록을 만든다."""
        source = self._get_json_absolute(
            f"{conf_base}/rest/api/content/{source_page_id}",
            params={"expand": "body.storage,space,ancestors,_links"},
        ) or {}

        storage = (source.get("body") or {}).get("storage") or {}
        source_body = storage.get("value")
        if source_body is None:
            raise ValueError(
                "원본 Confluence 회의록의 storage 본문을 읽지 못했습니다. "
                "페이지 조회 권한과 Confluence REST API 권한을 확인하세요."
            )

        cloned_body = self._replace_meeting_minutes_body(
            body=source_body,
            old_title=old_title,
            new_title=new_title,
            old_meeting_date=meeting_info["date"],
            new_meeting_date=meeting_info["next_date"],
        )
        representation = storage.get("representation") or "storage"

        payload = {
            "type": "page",
            "title": new_title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": cloned_body,
                    "representation": representation,
                }
            },
        }
        if target_parent_id:
            payload["ancestors"] = [{"id": str(target_parent_id)}]

        created = self._post_json_absolute(f"{conf_base}/rest/api/content", payload) or {}
        created_id = str(created.get("id") or "").strip()
        if not created_id:
            raise ValueError("Confluence 회의록 생성 후 page id를 받지 못했습니다.")
        return created

    def create_next_week_meeting_minutes_page_from_url(self, page_url: str) -> dict:
        """현재 SCCB 회의록을 Storage 복제로 다음 주 회의록으로 만든다.

        - 제목의 회의일을 7일 이동한다.
        - 날짜/시간 표의 날짜를 다음 회의일로 이동한다.
        - SCCB 리스트 Jira 매크로의 SCCB 완료일 범위를 7일 이동한다.
        - 연도 전환 시 ``YY년 회의록 (SCCB)`` 상위 페이지를 만들거나 재사용한다.
        """
        page_id = self._extract_confluence_page_id(page_url)
        if not page_id:
            raise ValueError("Confluence 회의록 page id를 URL에서 찾지 못했습니다.")

        conf_base = self._extract_confluence_base_url(page_url)
        if not conf_base:
            raise ValueError("Confluence base url을 URL에서 찾지 못했습니다.")

        src = self._get_json_absolute(
            f"{conf_base}/rest/api/content/{page_id}",
            params={"expand": "title,space,ancestors"},
        ) or {}

        old_title = src.get("title") or ""
        source_parent_id = self._get_page_parent_id(src)
        source_parent = ((src.get("ancestors") or [])[-1] if (src.get("ancestors") or []) else {}) or {}
        source_parent_title = str(source_parent.get("title") or "").strip()
        new_title, meeting_info = self._make_next_meeting_minutes_title(
            old_title,
            parent_title=source_parent_title,
        )

        space_key = ((src.get("space") or {}).get("key") or "").strip()
        if not space_key:
            raise ValueError("원본 회의록의 space key를 찾지 못했습니다.")

        target_parent_id = source_parent_id
        yearly_parent_title = ""
        yearly_parent_created = False

        if meeting_info["next_date"].year != meeting_info["date"].year:
            yearly_parent_title = self._make_yearly_meeting_minutes_parent_title(
                meeting_info["next_date"].year
            )
            yearly_parent_parent_id = self._get_page_grandparent_id(src)
            existing_yearly_parent = self._find_existing_confluence_sibling_page(
                conf_base=conf_base,
                space_key=space_key,
                title=yearly_parent_title,
                parent_id=yearly_parent_parent_id,
            )
            if existing_yearly_parent:
                target_parent_id = str(existing_yearly_parent.get("id") or "").strip()
            else:
                yearly_parent_payload = {
                    "type": "page",
                    "title": yearly_parent_title,
                    "space": {"key": space_key},
                    "body": {
                        "storage": {
                            "value": "<p></p>",
                            "representation": "storage",
                        }
                    },
                }
                if yearly_parent_parent_id:
                    yearly_parent_payload["ancestors"] = [{"id": yearly_parent_parent_id}]

                created_yearly_parent = self._post_json_absolute(
                    f"{conf_base}/rest/api/content",
                    yearly_parent_payload,
                ) or {}
                target_parent_id = str(created_yearly_parent.get("id") or "").strip()
                if not target_parent_id:
                    raise ValueError("새 연도 회의록 상위 페이지 생성 후 page id를 받지 못했습니다.")
                yearly_parent_created = True

        existing_page = self._find_existing_confluence_sibling_page(
            conf_base=conf_base,
            space_key=space_key,
            title=new_title,
            parent_id=target_parent_id,
        )
        if existing_page:
            return {
                "created": False,
                "title": new_title,
                "url": self._confluence_page_url(conf_base, existing_page),
                "id": existing_page.get("id"),
                "source_title": old_title,
                "yearly_parent_title": yearly_parent_title,
                "yearly_parent_created": yearly_parent_created,
                "copy_mode": "existing",
            }

        created = self._clone_confluence_meeting_minutes_storage(
            conf_base=conf_base,
            source_page_id=page_id,
            target_parent_id=target_parent_id,
            space_key=space_key,
            old_title=old_title,
            new_title=new_title,
            meeting_info=meeting_info,
        )
        created_page_id = str(created.get("id") or "").strip()
        return {
            "created": True,
            "title": new_title,
            "url": self._confluence_page_url(conf_base, created)
            or f"{conf_base}/pages/viewpage.action?pageId={created_page_id}",
            "id": created_page_id,
            "source_title": old_title,
            "yearly_parent_title": yearly_parent_title,
            "yearly_parent_created": yearly_parent_created,
            "copy_mode": "storage_clone",
        }

    def create_next_week_confluence_page_from_url(self, page_url: str) -> dict:
        """
        현재 주간 SCCB 페이지를 **원본 Storage 복제**로 다음 주차에 생성한다.

        처리 내용
        - 일반 주: 원본과 같은 연도별 상위 페이지 아래에 다음 주 페이지를 복제한다.
        - 연말/연초: 다음 주 시작일의 연도가 바뀌면 ``YY년 사전 SCCB 기록``
          상위 페이지를 같은 레벨에 만들거나 재사용하고, 그 아래에서 1W부터 복제한다.
        - 원본의 표/매크로/레이아웃을 담은 storage 본문을 그대로 사용하고, 제목과
          주간 날짜 범위만 생성 전에 최소 변경한다.
        - 이미 같은 상위 페이지 아래에 같은 제목이 있으면 중복 생성하지 않고 기존 페이지를 반환한다.
        """
        page_id = self._extract_confluence_page_id(page_url)
        if not page_id:
            raise ValueError("Confluence page id를 URL에서 찾지 못했습니다.")

        conf_base = self._extract_confluence_base_url(page_url)
        if not conf_base:
            raise ValueError("Confluence base url을 URL에서 찾지 못했습니다.")

        src = self._get_json_absolute(
            f"{conf_base}/rest/api/content/{page_id}",
            params={"expand": "title,space,ancestors"},
        ) or {}

        old_title = src.get("title") or ""
        new_title, week_info = self._make_next_week_title(old_title)
        space_key = ((src.get("space") or {}).get("key") or "").strip()
        if not space_key:
            raise ValueError("원본 페이지의 space key를 찾지 못했습니다.")

        source_parent_id = self._get_page_parent_id(src)
        source_parent = ((src.get("ancestors") or [])[-1] if (src.get("ancestors") or []) else {}) or {}
        target_parent_id = source_parent_id
        target_parent_title = str(source_parent.get("title") or "").strip()
        yearly_parent_title = ""
        yearly_parent_created = False

        # 2026년 마지막 주 -> 2027년 1W처럼 캘린더 연도가 넘어갈 때는,
        # 기존 연도별 상위 페이지와 같은 레벨에 새 연도별 SCCB 기록 페이지를 둔다.
        if week_info["next_start"].year != week_info["start"].year:
            yearly_parent_title = self._make_yearly_sccb_parent_title(week_info["next_start"].year)
            yearly_parent_parent_id = self._get_page_grandparent_id(src)
            existing_yearly_parent = self._find_existing_confluence_sibling_page(
                conf_base=conf_base,
                space_key=space_key,
                title=yearly_parent_title,
                parent_id=yearly_parent_parent_id,
            )
            if existing_yearly_parent:
                target_parent_id = str(existing_yearly_parent.get("id") or "").strip()
                target_parent_title = str(existing_yearly_parent.get("title") or yearly_parent_title).strip()
            else:
                yearly_parent_payload = {
                    "type": "page",
                    "title": yearly_parent_title,
                    "space": {"key": space_key},
                    "body": {
                        "storage": {
                            "value": "<p></p>",
                            "representation": "storage",
                        }
                    },
                }
                if yearly_parent_parent_id:
                    yearly_parent_payload["ancestors"] = [{"id": yearly_parent_parent_id}]

                created_yearly_parent = self._post_json_absolute(
                    f"{conf_base}/rest/api/content", yearly_parent_payload
                ) or {}
                target_parent_id = str(created_yearly_parent.get("id") or "").strip()
                if not target_parent_id:
                    raise ValueError("새 연도 SCCB 기록 상위 페이지 생성 후 page id를 받지 못했습니다.")
                yearly_parent_created = True
                target_parent_title = yearly_parent_title

        existing_page = self._find_existing_confluence_sibling_page(
            conf_base=conf_base,
            space_key=space_key,
            title=new_title,
            parent_id=target_parent_id,
        )
        if existing_page:
            return {
                "created": False,
                "title": new_title,
                "url": self._confluence_page_url(conf_base, existing_page),
                "id": existing_page.get("id"),
                "source_title": old_title,
                "yearly_parent_title": yearly_parent_title,
                "yearly_parent_created": yearly_parent_created,
                "copy_mode": "existing",
            }

        copied = self._clone_confluence_page_storage(
            conf_base=conf_base,
            source_page_id=page_id,
            target_parent_id=target_parent_id,
            space_key=space_key,
            old_title=old_title,
            new_title=new_title,
            week_info=week_info,
        )
        copied_page_id = str(copied.get("id") or "").strip()

        return {
            "created": True,
            "title": new_title,
            "url": self._confluence_page_url(conf_base, copied) or f"{conf_base}/pages/viewpage.action?pageId={copied_page_id}",
            "id": copied_page_id,
            "source_title": old_title,
            "yearly_parent_title": yearly_parent_title,
            "yearly_parent_created": yearly_parent_created,
            "copy_mode": "storage_clone",
        }

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
