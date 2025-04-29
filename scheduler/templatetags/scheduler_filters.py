from django import template
import math
from collections import defaultdict
import numpy as np

register = template.Library()

@register.filter
def filter_nurse_date(schedules, args):
    """
    간호사와 날짜를 기준으로 근무 일정 조회
    """
    nurse, date = args.split(',')
    return schedules.filter(nurse=nurse, date=date).first()

@register.filter
def get_item(dictionary, key):
    """딕셔너리에서 키를 통해 값을 가져오는 템플릿 필터"""
    return dictionary.get(key, 0)

@register.filter
def count_shifts(schedule_data, nurse_id):
    """
    간호사별 근무 유형 횟수를 카운트하는 필터
    """
    if nurse_id not in schedule_data:
        return {'D': 0, 'E': 0, 'N': 0, 'OFF': 0}
    
    nurse_schedule = schedule_data[nurse_id]
    counts = {'D': 0, 'E': 0, 'N': 0, 'OFF': 0}
    
    for date, shift in nurse_schedule.items():
        if shift in counts:
            counts[shift] += 1
    
    # 총 근무일 계산 (OFF 제외)
    counts['total_work'] = counts['D'] + counts['E'] + counts['N']
    
    # 백분율 계산 (프로그레스 바용)
    total_days = len(nurse_schedule)  # 총 일수
    if total_days > 0:
        counts['D_percent'] = min(100, round(counts['D'] / total_days * 100))
        counts['E_percent'] = min(100, round(counts['E'] / total_days * 100))
        counts['N_percent'] = min(100, round(counts['N'] / total_days * 100))
        counts['OFF_percent'] = min(100, round(counts['OFF'] / total_days * 100))
    else:
        counts['D_percent'] = 0
        counts['E_percent'] = 0
        counts['N_percent'] = 0
        counts['OFF_percent'] = 0
    
    return counts

@register.filter
def collect_shift_stats(schedule_data, shift_type):
    """
    특정 근무 유형의 통계 정보를 계산하는 필터
    """
    if not schedule_data:
        return {'avg': 0, 'min': 0, 'max': 0, 'std': 0}
    
    counts = []
    for nurse_id in schedule_data:
        nurse_schedule = schedule_data[nurse_id]
        shift_count = 0
        
        for date, shift in nurse_schedule.items():
            if shift == shift_type:
                shift_count += 1
        
        counts.append(shift_count)
    
    if not counts:
        return {'avg': 0, 'min': 0, 'max': 0, 'std': 0}
    
    # 통계 계산
    avg = sum(counts) / len(counts)
    min_val = min(counts)
    max_val = max(counts)
    
    # 표준편차 계산
    variance = sum((x - avg) ** 2 for x in counts) / len(counts)
    std_dev = math.sqrt(variance)
    
    return {
        'avg': avg,
        'min': min_val,
        'max': max_val,
        'std': std_dev
    }

@register.filter
def calculate_balance_score(schedule_data):
    """
    근무표의 균형 점수를 계산하는 함수 (100점 만점)
    - 각 근무 타입 분배의 표준편차 점수 (0~60점)
    - 간호사별 총 근무일수 비율 점수 (0~20점)
    - 총 근무일 균형 점수 (0~20점)
    """
    if not schedule_data:
        return 0
    
    # 간호사별 근무 타입 카운트
    nurse_shift_counts = defaultdict(lambda: {'D': 0, 'E': 0, 'N': 0, 'OFF': 0, 'total_work': 0})
    
    for nurse_id, dates in schedule_data.items():
        for date, shift in dates.items():
            nurse_shift_counts[nurse_id][shift] += 1
            if shift != 'OFF':
                nurse_shift_counts[nurse_id]['total_work'] += 1
    
    # 각 근무 유형별 표준편차 계산
    shift_std_devs = {}
    total_std_dev = 0
    
    for shift_type in ['D', 'E', 'N', 'OFF']:
        counts = [counts[shift_type] for nurse_id, counts in nurse_shift_counts.items()]
        if counts:
            std_dev = np.std(counts)
            shift_std_devs[shift_type] = std_dev
            total_std_dev += std_dev
    
    # 표준편차 점수 (낮을수록 좋음, 최대 60점)
    max_possible_std = len(nurse_shift_counts) * 0.5  # 이론적 최대 표준편차 추정값
    std_score = max(0, 60 - (total_std_dev / max_possible_std * 60))
    
    # 간호사별 근무 유형 비율 계산
    shift_ratios = []
    for nurse_id, counts in nurse_shift_counts.items():
        total = sum(counts[shift] for shift in ['D', 'E', 'N'])
        if total > 0:
            d_ratio = counts['D'] / total
            e_ratio = counts['E'] / total
            n_ratio = counts['N'] / total
            
            # 이상적인 비율: 각 1/3씩
            ideal_ratio = 1/3
            ratio_diff = (abs(d_ratio - ideal_ratio) + abs(e_ratio - ideal_ratio) + abs(n_ratio - ideal_ratio))
            shift_ratios.append(ratio_diff)
    
    # 근무 유형 비율 점수 (차이가 적을수록 좋음, 최대 20점)
    if shift_ratios:
        avg_ratio_diff = sum(shift_ratios) / len(shift_ratios)
        ratio_score = max(0, 20 - (avg_ratio_diff * 30))  # 최대 차이가 2/3일 때 0점
    else:
        ratio_score = 0
    
    # 간호사별 총 근무일수 표준편차 계산
    total_work_counts = [counts['total_work'] for nurse_id, counts in nurse_shift_counts.items()]
    
    if total_work_counts:
        total_work_std = np.std(total_work_counts)
    else:
        total_work_std = 0
    
    # 총 근무일 균형 점수 (0~20점)
    work_balance_score = max(0, 20 - (total_work_std * 5))
    
    # 최종 점수 (100점 만점)
    final_score = std_score + ratio_score + work_balance_score
    
    return final_score 