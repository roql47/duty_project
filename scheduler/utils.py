from datetime import datetime, timedelta
from collections import defaultdict, Counter
from .models import Nurse, Schedule, ShiftAssignment
from django.db.models import Count

def analyze_schedule(schedules, nurses, start_date, end_date, shift_requirements):
    """
    근무표를 분석하여 문제점을 찾아내는 함수
    
    Args:
        schedules: 분석할 스케줄 QuerySet
        nurses: 간호사 QuerySet
        start_date: 분석 시작 날짜
        end_date: 분석 종료 날짜
        shift_requirements: 각 근무별 필요 인원 수 {'D': int, 'E': int, 'N': int}
    
    Returns:
        분석 결과 사전:
        {
            'problems': 발견된 문제점 목록
            'nurse_stats': 간호사별 근무 통계
            'daily_stats': 일별 근무 통계
            'shift_requirements': 각 근무별 필요 인원 수
        }
    """
    # 결과 저장용 데이터 구조
    result = {
        'problems': [],
        'nurse_stats': {},
        'daily_stats': {},
        'shift_requirements': shift_requirements
    }
    
    # 근무표 데이터 구조화
    schedule_data = {}
    for schedule in schedules:
        assignments = schedule.shiftassignment_set.all()
        for assignment in assignments:
            nurse_id = assignment.nurse.id
            date = assignment.date
            shift = assignment.shift
            
            if nurse_id not in schedule_data:
                schedule_data[nurse_id] = {}
            
            schedule_data[nurse_id][date] = shift
    
    # 간호사별 통계 초기화
    for nurse in nurses:
        result['nurse_stats'][nurse.id] = {
            'name': nurse.name,
            'D': 0,
            'E': 0,
            'N': 0,
            'OFF': 0,
            'total_work_days': 0
        }
    
    # 일별 통계 초기화
    current_date = start_date
    while current_date <= end_date:
        result['daily_stats'][current_date] = {
            'D': 0,
            'E': 0,
            'N': 0,
            'OFF': 0
        }
        current_date += timedelta(days=1)
    
    # 근무표 분석 수행
    for nurse_id, dates in schedule_data.items():
        nurse = nurses.get(id=nurse_id)
        
        # 연속 근무일 검사를 위한 변수
        consecutive_work_days = 0
        consecutive_work_start = None
        
        # 주간 근무일 검사를 위한 변수
        weekly_work_days = defaultdict(int)
        
        # N 근무 패턴 검사를 위한 변수
        n_shift_dates = []
        
        # 날짜별로 분석
        current_date = start_date
        while current_date <= end_date:
            shift = dates.get(current_date, 'OFF')
            
            # 간호사별 통계 갱신
            result['nurse_stats'][nurse_id][shift] += 1
            if shift != 'OFF':
                result['nurse_stats'][nurse_id]['total_work_days'] += 1
            
            # 일별 통계 갱신
            result['daily_stats'][current_date][shift] += 1
            
            # 스태핑 요구사항 충족 검사
            if shift in ['D', 'E', 'N'] and result['daily_stats'][current_date][shift] < shift_requirements[shift]:
                # 스태핑 부족 문제 기록
                if current_date == end_date or dates.get(current_date + timedelta(days=1), None) is not None:
                    result['problems'].append({
                        'type': 'understaffed',
                        'date': current_date,
                        'shift': shift,
                        'required': shift_requirements[shift],
                        'actual': result['daily_stats'][current_date][shift],
                        'shortage': shift_requirements[shift] - result['daily_stats'][current_date][shift]
                    })
            
            # 연속 근무일 검사
            if shift != 'OFF':
                if consecutive_work_days == 0:
                    consecutive_work_start = current_date
                consecutive_work_days += 1
                
                # 주간 근무일 계산 (월요일 기준)
                week_start = current_date - timedelta(days=current_date.weekday())
                weekly_work_days[week_start] += 1
            else:
                # 연속 근무일 초과 문제 기록
                if consecutive_work_days > 6:
                    result['problems'].append({
                        'type': 'consecutive_work',
                        'nurse_id': nurse_id,
                        'nurse_name': nurse.name,
                        'start_date': consecutive_work_start,
                        'end_date': current_date - timedelta(days=1),
                        'days': consecutive_work_days
                    })
                consecutive_work_days = 0
            
            # N 근무 패턴 검사
            if shift == 'N':
                n_shift_dates.append(current_date)
            
            # E 다음 D 패턴 검사 (직접 또는 OFF를 사이에 두고)
            if shift == 'D' and current_date > start_date:
                prev_date = current_date - timedelta(days=1)
                prev_shift = dates.get(prev_date, 'OFF')
                
                if prev_shift == 'E':
                    # E 다음 바로 D
                    result['problems'].append({
                        'type': 'e_d_pattern',
                        'nurse_id': nurse_id,
                        'nurse_name': nurse.name,
                        'e_date': prev_date,
                        'd_date': current_date,
                        'has_off_between': False
                    })
                elif prev_shift == 'OFF' and prev_date > start_date:
                    # E-OFF-D 패턴 검사
                    prev_prev_date = prev_date - timedelta(days=1)
                    prev_prev_shift = dates.get(prev_prev_date, 'OFF')
                    
                    if prev_prev_shift == 'E':
                        result['problems'].append({
                            'type': 'e_d_pattern',
                            'nurse_id': nurse_id,
                            'nurse_name': nurse.name,
                            'e_date': prev_prev_date,
                            'off_date': prev_date,
                            'd_date': current_date,
                            'has_off_between': True
                        })
            
            current_date += timedelta(days=1)
        
        # 마지막 날짜에 대한 연속 근무일 검사
        if consecutive_work_days > 6:
            result['problems'].append({
                'type': 'consecutive_work',
                'nurse_id': nurse_id,
                'nurse_name': nurse.name,
                'start_date': consecutive_work_start,
                'end_date': end_date,
                'days': consecutive_work_days
            })
        
        # 주간 근무일 검사
        for week_start, days in weekly_work_days.items():
            if days > 5:
                result['problems'].append({
                    'type': 'weekly_work',
                    'nurse_id': nurse_id,
                    'nurse_name': nurse.name,
                    'week_start': week_start,
                    'week_end': week_start + timedelta(days=6),
                    'days': days
                })
        
        # N 근무 패턴 검사 (단일 N 또는 OFF-N-OFF)
        if n_shift_dates:
            for i, n_date in enumerate(n_shift_dates):
                # 단일 N 검사
                is_single_n = False
                if i == 0 and i == len(n_shift_dates) - 1:
                    # 유일한 N 근무
                    is_single_n = True
                elif i == 0:
                    # 첫 번째 N이고, 다음 날짜와 연속되지 않음
                    if (n_shift_dates[i+1] - n_date).days != 1:
                        is_single_n = True
                elif i == len(n_shift_dates) - 1:
                    # 마지막 N이고, 이전 날짜와 연속되지 않음
                    if (n_date - n_shift_dates[i-1]).days != 1:
                        is_single_n = True
                else:
                    # 중간 N이고, 이전, 다음과 연속되지 않음
                    if (n_date - n_shift_dates[i-1]).days != 1 and (n_shift_dates[i+1] - n_date).days != 1:
                        is_single_n = True
                
                if is_single_n:
                    result['problems'].append({
                        'type': 'single_n',
                        'nurse_id': nurse_id,
                        'nurse_name': nurse.name,
                        'n_date': n_date,
                        'pattern_type': 'single_n'
                    })
                
                # OFF-N-OFF 패턴 검사
                if n_date > start_date and n_date < end_date:
                    prev_date = n_date - timedelta(days=1)
                    next_date = n_date + timedelta(days=1)
                    prev_shift = dates.get(prev_date, 'OFF')
                    next_shift = dates.get(next_date, 'OFF')
                    
                    if prev_shift == 'OFF' and next_shift == 'OFF':
                        result['problems'].append({
                            'type': 'single_n',
                            'nurse_id': nurse_id,
                            'nurse_name': nurse.name,
                            'prev_date': prev_date,
                            'n_date': n_date,
                            'next_date': next_date,
                            'pattern_type': 'off_n_off'
                        })
    
    # N 근무 후 OFF가 아닌 경우 검사
    for nurse_id, dates in schedule_data.items():
        nurse = nurses.get(id=nurse_id)
        date_list = sorted(dates.keys())
        
        for i, date in enumerate(date_list):
            if dates[date] == 'N' and i < len(date_list) - 1:
                next_date = date_list[i + 1]
                if (next_date - date).days == 1 and dates[next_date] != 'OFF':
                    result['problems'].append({
                        'type': 'n_without_off',
                        'nurse_id': nurse_id,
                        'nurse_name': nurse.name,
                        'n_date': date,
                        'next_date': next_date,
                        'next_shift': dates[next_date]
                    })
    
    # 문제 유형별로 분류
    return result

def get_schedule_statistics(schedules, nurses, start_date, end_date):
    """
    스케줄의 근무 분포 통계를 계산
    """
    stats = {
        'nurse_stats': {},
        'shift_counts': {'D': 0, 'E': 0, 'N': 0, 'OFF': 0},
        'distribution_score': 0
    }
    
    # 간호사별 근무 카운트 초기화
    for nurse in nurses:
        stats['nurse_stats'][nurse.id] = {
            'name': nurse.name,
            'shift_counts': {'D': 0, 'E': 0, 'N': 0, 'OFF': 0},
            'total_work_days': 0
        }
    
    # 근무 집계
    for schedule in schedules:
        assignments = schedule.shiftassignment_set.all()
        for assignment in assignments:
            nurse_id = assignment.nurse.id
            shift = assignment.shift
            
            # 간호사별 통계 갱신
            stats['nurse_stats'][nurse_id]['shift_counts'][shift] += 1
            if shift != 'OFF':
                stats['nurse_stats'][nurse_id]['total_work_days'] += 1
            
            # 전체 통계 갱신
            stats['shift_counts'][shift] += 1
    
    # 근무 분포 점수 계산 (표준편차 기반)
    work_days = []
    for nurse_id, nurse_stat in stats['nurse_stats'].items():
        work_days.append(nurse_stat['total_work_days'])
    
    if work_days:
        # 평균 근무일 계산
        avg_work_days = sum(work_days) / len(work_days)
        
        # 표준편차 계산
        variance = sum((days - avg_work_days) ** 2 for days in work_days) / len(work_days)
        std_dev = variance ** 0.5
        
        # 표준편차가 0이면 완벽한 분포 (1.0 - 100%)
        if std_dev == 0:
            stats['distribution_score'] = 1.0
        else:
            # 표준편차가 클수록 분포가 불균등 (점수 낮음)
            # 표준편차가 작을수록 분포가 균등 (점수 높음)
            # 최대 표준편차 추정 (간호사 수에 따라 달라짐)
            max_std_dev = (end_date - start_date).days / 2
            stats['distribution_score'] = max(0, 1.0 - (std_dev / max_std_dev))
    
    return stats 