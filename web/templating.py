"""공용 Jinja2 템플릿 인스턴스.

라우트마다 따로 만들지 않고 여기서 한 번만 만들어 전 라우트가 공유한다. base.html의
네비게이션 등 공통 템플릿 설정을 한 곳에서 관리하기 위한 단일 진입점이다.
"""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="web/templates")
