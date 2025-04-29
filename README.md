# 간호사 스케줄링 시스템

간호사들의 근무 일정을 자동으로 생성하고 관리하는 웹 기반 시스템입니다.

## 주요 기능

- **자동 스케줄 생성**: 간호사의 선호도, 휴가 신청, 스킬 등을 고려한 최적의 근무표 자동 생성
- **휴가 관리**: 간호사의 휴가 요청 처리 및 관리
- **근무 패턴 분석**: 간호사의 근무 패턴 분석 및 최적화
- **스케줄 재생성**: 기존 스케줄 삭제 후 새로운 조건으로 재생성 가능
- **스케줄 삭제**: 모든 근무표 삭제 기능

## 사용 방법

1. 스케줄 생성
   - `generate_schedule` 기능을 통해 간호사 근무표 생성
   - 간호사의 선호도와 스킬을 고려하여 최적의 일정 배정

2. 스케줄 관리
   - `view_schedule`로 현재 근무표 확인
   - `regenerate_schedule`로 필요 시 스케줄 재생성
   - `delete_schedule`로 모든 근무표 초기화

3. 분석
   - `analyze_schedule_view`를 통해 생성된 스케줄의 품질 분석

## GitHub 저장소 사용 방법

1. 저장소 클론
   ```
   git clone https://github.com/[사용자명]/[저장소명].git
   cd [저장소명]
   ```

2. 가상 환경 설정 및 의존성 설치
   ```
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. 데이터베이스 설정
   ```
   python manage.py migrate
   ```

4. 개발 서버 실행
   ```
   python manage.py runserver
   ```

5. 관리자 계정 생성 (선택 사항)
   ```
   python manage.py createsuperuser
   ```

6. 브라우저에서 접속
   - 개발 서버: `http://127.0.0.1:8000/`
   - 관리자 페이지: `http://127.0.0.1:8000/admin/`

## 시스템 요구사항

- Python
- Django 웹 프레임워크
- 데이터베이스 (SQLite/PostgreSQL)

## 기술적 특징

- 간호사 근무 패턴 점수 계산 (`calculate_pattern_score`)
- 최적의 근무 할당 알고리즘 (`assign_optimal_shift`)
- 휴일 및 요청 휴무일 관리 (`is_holiday`, `get_wanted_offs_for_nurses`)
- 근무 할당 유효성 검사 (`is_valid_assignment`) 