from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Nurse, Schedule, StaffingRequirement, ShiftChangeHistory
from datetime import datetime, timedelta
import random
from collections import defaultdict, Counter
from django.db import models
import heapq
import copy
from django.http import JsonResponse
import uuid

# Create your views here.

def update_staffing(request, pk):
    """근무별 필요 인원 설정 수정"""
    if request.method == 'POST':
        shift = request.POST.get('shift')
        required_staff = int(request.POST.get('required_staff'))
        
        if pk > 0:  # 기존 설정 수정
            requirement = get_object_or_404(StaffingRequirement, pk=pk)
            requirement.required_staff = required_staff
            requirement.save()
            messages.success(request, f'{requirement.get_shift_display()} 근무 인원이 {required_staff}명으로 수정되었습니다.')
        else:  # 새로운 설정 추가
            requirement, created = StaffingRequirement.objects.update_or_create(
                shift=shift,
                defaults={'required_staff': required_staff}
            )
            messages.success(request, f'{requirement.get_shift_display()} 근무 인원이 {required_staff}명으로 설정되었습니다.')
        
        return redirect('generate_schedule')
    
    # GET 요청 처리 (수정 폼)
    if pk > 0:
        requirement = get_object_or_404(StaffingRequirement, pk=pk)
        context = {'requirement': requirement}
        return render(request, 'scheduler/update_staffing.html', context)
    
    # 잘못된 요청
    messages.error(request, '잘못된 요청입니다.')
    return redirect('generate_schedule')

def get_wanted_offs_for_nurses(nurses, start_date, end_date):
    """
    간호사별 원티드 OFF 날짜를 반환하는 함수
    실제로는 DB나 다른 소스에서 읽어와야 함.
    반환 형식: {nurse_id: {date1, date2, ...}}
    """
    wanted_offs = defaultdict(set)
    # 예시: 특정 간호사가 특정 날짜에 OFF를 원한다고 가정
    nurse_ids = [n.id for n in nurses]
    
    # 예시 데이터 - 실제로는 DB에서 가져와야 함
    if nurse_ids:
        # 모든 간호사에게 무작위로 2-3개의 원티드 OFF 날짜 배정
        for nurse_id in nurse_ids:
            # 날짜 범위에서 무작위로 2-3일 선택
            days_count = random.randint(2, 3)
            date_range = []
            current_date = start_date
            while current_date <= end_date:
                date_range.append(current_date)
                current_date += timedelta(days=1)
                
            if date_range:
                # 무작위로 날짜 선택
                wanted_days = random.sample(date_range, min(days_count, len(date_range)))
                for day in wanted_days:
                    wanted_offs[nurse_id].add(day)
    
    return wanted_offs

# 공휴일/전체 휴무일 확인 함수
def is_holiday(date):
    """
    공휴일 또는 전체 휴무일인지 확인하는 함수.
    사용자 요청에 따라 공휴일/일요일도 일반 근무일로 처리하기 위해 항상 False 반환
    """
    # 모든 날짜를 일반 근무일로 취급
    return False

def generate_schedule(request, regenerate=False):
    """근무표 생성 - 우선순위 큐, 백트래킹, 분할 정복 방식을 활용한 스케줄링"""
    staffing_requirements = StaffingRequirement.objects.all()
    nurses = Nurse.objects.all()
    nurse_list = list(nurses)
    
    # 재생성 모드인 경우
    if regenerate:
        # 먼저 기존 근무표의 시작/종료 날짜와 간호사별 배정 근무 수를 가져옴
        all_schedules = Schedule.objects.all().order_by('date')
        if not all_schedules.exists():
            messages.error(request, '재생성할 근무표가 없습니다.')
            return redirect('view_schedule')
            
        min_date = all_schedules.order_by('date').first().date
        max_date = all_schedules.order_by('-date').first().date
        
        # 간호사별 배정된 근무 수 계산
        nurse_shifts = {}
        for nurse in nurse_list:
            d_count = Schedule.objects.filter(nurse=nurse, shift='D').count()
            e_count = Schedule.objects.filter(nurse=nurse, shift='E').count()
            n_count = Schedule.objects.filter(nurse=nurse, shift='N').count()
            nurse_shifts[nurse.id] = d_count + e_count + n_count
            
        start_date = min_date
        end_date = max_date
        
        # 근무별 필요 인원 설정 계산
        shift_requirements = {}
        for req in staffing_requirements:
            shift_requirements[req.shift] = req.required_staff
        
        # 기본값 설정
        if 'D' not in shift_requirements: shift_requirements['D'] = 1
        if 'E' not in shift_requirements: shift_requirements['E'] = 1
        if 'N' not in shift_requirements: shift_requirements['N'] = 1
        
        # 스케줄 생성 로직 호출
        create_schedule_with_pattern(request, start_date, end_date, nurse_list, nurse_shifts, shift_requirements)
        return redirect('view_schedule')
    
    if request.method == 'POST':
        # 나이트 킵 간호사 설정 처리
        if 'setup_shifts' in request.POST:
            # 나이트 킵 간호사 설정 업데이트
            for nurse in nurse_list:
                night_keeper_key = f'night_keeper_{nurse.id}'
                is_night_keeper = night_keeper_key in request.POST
                
                # 기존 설정과 다를 경우만 업데이트하여 DB 부담 최소화
                if nurse.is_night_keeper != is_night_keeper:
                    nurse.is_night_keeper = is_night_keeper
                    nurse.save()
                    
                    if is_night_keeper:
                        messages.info(request, f'{nurse.name} 간호사가 나이트 킵으로 설정되었습니다.')
                    else:
                        messages.info(request, f'{nurse.name} 간호사의 나이트 킵 설정이 해제되었습니다.')
        
            start_date = datetime.strptime(request.POST['start_date'], '%Y-%m-%d')
            end_date = datetime.strptime(request.POST['end_date'], '%Y-%m-%d')
            
            # 기본 유효성 검사
            if end_date < start_date:
                messages.error(request, '종료 날짜는 시작 날짜보다 나중이어야 합니다.')
                return render(request, 'scheduler/generate_schedule.html', {'staffing_requirements': staffing_requirements})
            
            if (end_date - start_date).days > 90:
                messages.error(request, '근무표는 최대 3개월(90일)까지만 생성할 수 있습니다.')
                return render(request, 'scheduler/generate_schedule.html', {'staffing_requirements': staffing_requirements})
            
            # 날짜 범위 계산
            date_range = []
            current_date = start_date
            while current_date <= end_date:
                date_range.append(current_date)
                current_date += timedelta(days=1)
            
            total_days = len(date_range)
            
            # 근무별 필요 인원 설정 계산
            shift_requirements = {}
            for req in staffing_requirements:
                shift_requirements[req.shift] = req.required_staff
            
            # 기본값 설정
            if 'D' not in shift_requirements: shift_requirements['D'] = 1
            if 'E' not in shift_requirements: shift_requirements['E'] = 1
            if 'N' not in shift_requirements: shift_requirements['N'] = 1
            
            # 총 필요 근무 슬롯 계산
            total_required_slots = 0
            slots_by_shift = {'D': 0, 'E': 0, 'N': 0}
            
            for shift_type in ['D', 'E', 'N']:
                required = shift_requirements.get(shift_type, 0)
                slots_by_shift[shift_type] = required * total_days
                total_required_slots += required * total_days
            
            # 나이트 킵 간호사 수 확인 및 N 근무 예약
            night_keepers = [nurse for nurse in nurse_list if nurse.is_night_keeper]
            night_keeper_count = len(night_keepers)
            
            # night_keeper_shifts 변수 초기화
            night_keeper_shifts = {}
            
            if night_keeper_count > 0:
                # 나이트 킵 간호사에게 모든 N 근무 할당
                n_required_per_day = shift_requirements.get('N', 0)
                total_n_slots = n_required_per_day * total_days
                
                if night_keeper_count >= n_required_per_day:
                    # 나이트 킵 간호사가 N 근무를 모두 커버할 수 있는 경우
                    messages.success(request, f'{night_keeper_count}명의 나이트 킵 간호사가 모든 N 근무를 담당합니다.')
                    
                    # 나이트 킵 간호사별 균등 N 근무 계산
                    n_per_night_keeper = total_n_slots // night_keeper_count
                    remaining_n = total_n_slots % night_keeper_count
                    
                    # 배정량 계산
                    for idx, nurse in enumerate(night_keepers):
                        # 나머지 N 근무를 순서대로 1개씩 추가 배정
                        extra = 1 if idx < remaining_n else 0
                        night_keeper_shifts[nurse.id] = n_per_night_keeper + extra
                        
                    # 일반 간호사들에게는 N 근무 없이 D, E만 배정
                    non_night_keepers = [nurse for nurse in nurse_list if not nurse.is_night_keeper]
                    remaining_required_slots = total_required_slots - total_n_slots
                    
                    if non_night_keepers:
                        avg_shifts_per_nurse = remaining_required_slots / len(non_night_keepers)
                        messages.info(request, f'일반 간호사({len(non_night_keepers)}명)는 N 근무 없이 평균 {avg_shifts_per_nurse:.1f}개의 D/E 근무를 담당합니다.')
                    else:
                        messages.warning(request, '모든 간호사가 나이트 킵으로 설정되어 D, E 근무를 담당할 간호사가 없습니다.')
                else:
                    # 나이트 킵 간호사가 N 근무를 모두 커버할 수 없는 경우
                    n_per_night_keeper = total_days  # 각 나이트 킵 간호사는 매일 N 근무
                    covered_n_slots = night_keeper_count * total_days
                    remaining_n_slots = total_n_slots - covered_n_slots
                    
                    messages.warning(request, f'나이트 킵 간호사({night_keeper_count}명)가 {covered_n_slots}개의 N 근무를 담당하며, 남은 {remaining_n_slots}개는 일반 간호사가 담당합니다.')
                    
                    for nurse in night_keepers:
                        night_keeper_shifts[nurse.id] = n_per_night_keeper
                
                # 간호사별 기본 균등 분배 근무수 계산
                nurse_shifts = {}
                remaining_shifts = total_required_slots
                
                # 1. 나이트 킵 간호사 먼저 배정
                for nurse in night_keepers:
                    nurse_shifts[nurse.id] = night_keeper_shifts.get(nurse.id, 0)
                    remaining_shifts -= nurse_shifts[nurse.id]
                
                # 2. 일반 간호사에게 나머지 근무 균등 배분
                non_night_keepers = [nurse for nurse in nurse_list if not nurse.is_night_keeper]
                non_keeper_count = len(non_night_keepers)
                
                if non_keeper_count > 0:
                    avg_shifts_per_nurse = remaining_shifts / non_keeper_count
                    
                    for i, nurse in enumerate(non_night_keepers):
                        if i == non_keeper_count - 1:  # 마지막 간호사는 나머지 모든 근무를 할당
                            nurse_shifts[nurse.id] = remaining_shifts
                        else:
                            # 균등하게 분배하되 정수로 내림
                            assigned = int(avg_shifts_per_nurse)
                            nurse_shifts[nurse.id] = assigned
                            remaining_shifts -= assigned
            else:
                # 나이트 킵 간호사가 없는 경우 기존 로직 그대로 진행
                # 간호사별 기본 균등 분배 근무수 계산
                nurse_count = len(nurse_list)
                avg_shifts_per_nurse = total_required_slots / nurse_count if nurse_count > 0 else 0
                
                # 기본 간호사별 근무 배분 계산
                nurse_shifts = {}
                remaining_shifts = total_required_slots
                
                for nurse in nurse_list:
                    if nurse == nurse_list[-1]:  # 마지막 간호사는 나머지 모든 근무를 할당
                        nurse_shifts[nurse.id] = remaining_shifts
                    else:
                        # 균등하게 분배하되 정수로 내림
                        assigned = int(avg_shifts_per_nurse)
                        nurse_shifts[nurse.id] = assigned
                        remaining_shifts -= assigned
            
            context = {
                'staffing_requirements': staffing_requirements,
                'nurses': nurse_list,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d'),
                'total_days': total_days,
                'total_required_slots': total_required_slots,
                'slots_by_shift': slots_by_shift,
                'nurse_shifts': nurse_shifts,
                'setup_mode': True
            }
            
            return render(request, 'scheduler/generate_schedule.html', context)
        
        # 간호사별 근무수 설정 후 실제 근무표 생성 모드
        elif 'create_schedule' in request.POST:
            start_date = datetime.strptime(request.POST['start_date'], '%Y-%m-%d')
            end_date = datetime.strptime(request.POST['end_date'], '%Y-%m-%d')
            
            # 날짜 범위 계산
            date_range = []
            current_date = start_date
            while current_date <= end_date:
                date_range.append(current_date)
                current_date += timedelta(days=1)
            
            total_days = len(date_range)
            
            # 근무별 필요 인원 설정 계산
            shift_requirements = {}
            for req in staffing_requirements:
                shift_requirements[req.shift] = req.required_staff
            
            # 기본값 설정
            if 'D' not in shift_requirements: shift_requirements['D'] = 1
            if 'E' not in shift_requirements: shift_requirements['E'] = 1
            if 'N' not in shift_requirements: shift_requirements['N'] = 1
            
            # 간호사별 할당된 근무수 가져오기
            nurse_shifts = {}
            total_assigned_shifts = 0
            
            for nurse in nurse_list:
                shift_count = int(request.POST.get(f'nurse_shift_{nurse.id}', 0))
                nurse_shifts[nurse.id] = shift_count
                total_assigned_shifts += shift_count
            
            # 총 필요 근무수 계산
            total_required_slots = 0
            for shift_type in ['D', 'E', 'N']:
                required = shift_requirements.get(shift_type, 0)
                total_required_slots += required * total_days
            
            # 근무수 검증
            if total_assigned_shifts != total_required_slots:
                messages.error(request, f'할당된 근무 수({total_assigned_shifts})가 필요한 총 근무 수({total_required_slots})와 일치하지 않습니다.')
                return redirect('generate_schedule')
            
            # 스케줄 생성 로직 호출
            create_schedule_with_pattern(request, start_date, end_date, nurse_list, nurse_shifts, shift_requirements)
            return redirect('view_schedule')
    
    return render(request, 'scheduler/generate_schedule.html', {
        'staffing_requirements': staffing_requirements,
        'nurses': nurse_list
    })

def create_schedule_with_pattern(request, start_date, end_date, nurse_list, nurse_shifts, shift_requirements):
    """패턴 기반으로 스케줄을 생성하는 함수"""
    try:
        # 먼저 해당 기간의 기존 스케줄을 삭제
        existing_schedules = Schedule.objects.filter(date__range=[start_date, end_date])
        if existing_schedules.exists():
            delete_count = existing_schedules.count()
            existing_schedules.delete()
            messages.info(request, f'기존 스케줄 {delete_count}개가 삭제되었습니다. 새 스케줄을 생성합니다.')
        
        # 전역 변수 선언
        final_schedule = {}  # 최종 스케줄 결과 (nurse_id, date) -> shift
        
        # 날짜 범위 생성
        date_range = []
        current_date = start_date
        while current_date <= end_date:
            date_range.append(current_date)
            current_date += timedelta(days=1)
        
        total_days = len(date_range)
        
        # 일자별 필요 인원 설정
        daily_shift_requirements = {day: {'D': 4, 'E': 4, 'N': 4} for day in date_range}  # 모든 교대에 필요 인원 4명으로 설정
        
        # 추가: 근무 유형 최소/최대 비율 설정 - 균형 있는 배정을 위함
        min_ratio_per_shift = 0.2  # 최소 20%는 각 유형의 근무가 배정되어야 함
        max_ratio_per_shift = 0.4  # 최대 40%까지만 각 유형의 근무가 배정될 수 있음
        
        # 추가: 간호사별 선호도 정보 초기화
        nurse_preferences = {nurse.id: {'D': 0, 'E': 0, 'N': 0, 'OFF': 0} for nurse in nurse_list}
        
        # 추가: 간호사별 휴가 요청 정보
        off_requests = set()  # (nurse_id, date) 형태의 휴가 요청 정보
        
        # 나이트 킵 간호사의 경우 N 근무 선호도 높게 설정
        for nurse in nurse_list:
            if nurse.is_night_keeper:
                nurse_preferences[nurse.id]['N'] = 10
                nurse_preferences[nurse.id]['D'] = 0
                nurse_preferences[nurse.id]['E'] = 0
        
        # 원하는 휴무 요청 불러오기
        wanted_offs = get_wanted_offs_for_nurses(nurse_list, start_date, end_date)
        for nurse_id, dates in wanted_offs.items():
            for date in dates:
                off_requests.add((nurse_id, date))
        
        # 분류: 나이트킵 간호사와 일반 간호사
        night_keepers = [nurse for nurse in nurse_list if nurse.is_night_keeper]
        regular_nurses = [nurse for nurse in nurse_list if not nurse.is_night_keeper]
        
        # 2. 간호사 정보 준비
        nurses_by_id = {nurse.id: nurse for nurse in nurse_list}
        
        # 목표 근무 균형 설정 - 일반 간호사들이 각 유형별로 비슷한 수의 근무를 갖도록 함
        target_shifts_per_nurse = {}
        regular_nurse_count = len(regular_nurses)
        
        if regular_nurse_count > 0:
            # 일반 간호사 수로 나눈 각 교대 당 목표 근무 횟수
            total_d_shifts = sum(daily_shift_requirements[day]['D'] for day in date_range)
            total_e_shifts = sum(daily_shift_requirements[day]['E'] for day in date_range)
            total_n_shifts = sum(daily_shift_requirements[day]['N'] for day in date_range) - (len(night_keepers) * len(date_range))
            
            # 나이트킵 간호사가 커버하고 남은 N 근무만 일반 간호사에게 배정
            if total_n_shifts < 0:
                total_n_shifts = 0
            
            # D와 E 근무는 가능한 균등하게 배분
            target_d_per_nurse = total_d_shifts / regular_nurse_count
            target_e_per_nurse = total_e_shifts / regular_nurse_count
            target_n_per_nurse = total_n_shifts / regular_nurse_count
            
            # D와 E 근무의 차이가 최대 2회 이내가 되도록 조정
            d_e_diff = abs(target_d_per_nurse - target_e_per_nurse)
            if d_e_diff > 2:
                # D와 E 근무를 균등하게 조정
                total_d_e_shifts = total_d_shifts + total_e_shifts
                target_d_per_nurse = total_d_e_shifts / (2 * regular_nurse_count)
                target_e_per_nurse = target_d_per_nurse
            
            messages.info(request, f'일반 간호사 목표 근무 배분: D={target_d_per_nurse:.1f}, E={target_e_per_nurse:.1f}, N={target_n_per_nurse:.1f}')
            
            for nurse in regular_nurses:
                target_shifts_per_nurse[nurse.id] = {
                    'D': target_d_per_nurse,
                    'E': target_e_per_nurse,
                    'N': target_n_per_nurse
                }
        
        # 3. 근무별 필요 인원 설정
        # 기본값 설정 (입력 없으면 각 근무별 1명으로 설정)
        if 'D' not in shift_requirements: shift_requirements['D'] = 4
        if 'E' not in shift_requirements: shift_requirements['E'] = 4
        if 'N' not in shift_requirements: shift_requirements['N'] = 4
        
        # 4. 일별 필요 근무 수 계산 (평일/주말 모두 동일 적용)
        daily_shift_requirements = {}
        for day in date_range:
            daily_shift_requirements[day] = {
                'D': shift_requirements.get('D', 4),
                'E': shift_requirements.get('E', 4),
                'N': 4  # N 근무는 항상 4명으로 고정
            }
            
        # 변수명 통일 (daily_requirements를 daily_shift_requirements로 사용)
        daily_shift_requirements = daily_shift_requirements
        
        # 5. 숙련도 기반 필요 인원 설정
        # 날짜별 각 근무별 필요한 숙련도 인원
        skill_requirements = {}
        for day in date_range:
            skill_requirements[day] = {}
            for shift_type in ['D', 'E', 'N']:
                required_staff = daily_shift_requirements[day][shift_type]
                skill_requirements[day][shift_type] = {
                    'high': 0,  # 숙련도 5-6
                    'mid': 0,   # 숙련도 3-4
                    'low': 0    # 숙련도 1-2
                }
                
                # 필요 인원에 따른 숙련도별 필요 인원 설정
                if required_staff == 3:
                    skill_requirements[day][shift_type]['high'] = 1
                    skill_requirements[day][shift_type]['mid'] = 1
                    skill_requirements[day][shift_type]['low'] = 1
                elif required_staff >= 4:
                    skill_requirements[day][shift_type]['high'] = 1
                    skill_requirements[day][shift_type]['mid'] = 1
                    skill_requirements[day][shift_type]['low'] = 1
                    # 나머지는 어떤 숙련도여도 상관없음
        
        # 숙련도 요구사항 업데이트 함수
        def update_skill_requirements(nurse_param, day, shift):
            """간호사의 숙련도에 따라 필요 인원 요구사항 업데이트"""
            nonlocal skill_requirements
            if shift not in ['D', 'E', 'N']:
                return True  # OFF 근무는 처리하지 않음
                
            # nurse_param이 nurse_id인지 nurse 객체인지 확인
            if isinstance(nurse_param, int):
                # nurse_id가 전달된 경우
                nurse_id = nurse_param
                try:
                    nurse = nurses_by_id.get(nurse_id)
                    if nurse is None:
                        # nurses_by_id에서 찾을 수 없는 경우 nurse_list에서 직접 찾기
                        nurse = next((n for n in nurse_list if n.id == nurse_id), None)
                    if nurse is None:
                        # 여전히 찾을 수 없다면 기본값으로 진행 (오류 방지)
                        return True
                    skill_level = nurse.skill_level
                except Exception as e:
                    # 오류 발생 시 로그 출력하고 기본값으로 진행
                    print(f"Error in update_skill_requirements: {e}, nurse_id={nurse_id}")
                    return True
            else:
                # nurse 객체가 전달된 경우
                nurse = nurse_param
                try:
                    skill_level = nurse.skill_level
                except AttributeError:
                    # nurse 객체에 skill_level 속성이 없는 경우
                    print(f"Error in update_skill_requirements: nurse object has no skill_level attribute, nurse={nurse}")
                    return True
            
            # 숙련도 카테고리 결정
            if skill_level >= 5:
                category = 'high'
            elif skill_level >= 3:
                category = 'mid'
            else:
                category = 'low'
                
            # 해당 카테고리의 요구사항 감소
            if skill_requirements[day][shift][category] > 0:
                skill_requirements[day][shift][category] -= 1
                return True
            # 해당 카테고리가 이미 충족된 경우, 다른 카테고리에서 감소
            else:
                # 숙련도 1인 경우는 추가 배정 가능
                if skill_level == 1:
                    return True  # 숙련도 1은 추가 배정 가능
                
                # 다른 카테고리에서 감소 (높은 숙련도부터 확인)
                categories = ['high', 'mid', 'low']
                for cat in categories:
                    if skill_requirements[day][shift][cat] > 0:
                        skill_requirements[day][shift][cat] -= 1
                        return True
                        
                # 모든 숙련도 범주의 요구사항이 충족된 경우
                if any(skill_requirements[day][shift][cat] > 0 for cat in ['high', 'mid', 'low']):
                    return False  # 다른 숙련도 범주의 요구사항이 남아있어 이 간호사 배정 불가
                
                # 모든 숙련도 범주의 필요 인원이 충족된 경우, 추가 인원으로 배정 가능
                return True
        
        # 나머지 코드는 그대로 유지...

        # 최적 근무 배정 함수
        def assign_optimal_shift(nurse_id, date):
            """간호사에게 최적의 근무를 배정하는 함수"""
            nonlocal final_schedule, nurse_shifts, last_5_shifts, daily_shift_requirements, target_shifts_per_nurse
            
            # 해당 날짜에 이미 배정되었는지 확인
            if (nurse_id, date) in final_schedule:
                return final_schedule[(nurse_id, date)]
            
            # 간호사 객체 찾기
            nurse = next((n for n in nurse_list if n.id == nurse_id), None)
            
            # 나이트킵 간호사라면 N 또는 OFF만 가능
            if nurse and nurse.is_night_keeper:
                if daily_shift_requirements[date]['N'] > 0 and is_valid_assignment(nurse_id, date, 'N'):
                    # N 근무 배정
                    final_schedule[(nurse_id, date)] = 'N'
                    nurse_shifts[nurse_id]['N'] = nurse_shifts[nurse_id].get('N', 0) + 1
                    daily_shift_requirements[date]['N'] -= 1
                    last_5_shifts[nurse_id] = last_5_shifts.get(nurse_id, [])[-4:] + ['N']
                    update_skill_requirements(nurse_id, date, 'N')
                    return 'N'
                else:
                    # OFF 배정
                    final_schedule[(nurse_id, date)] = 'OFF'
                    nurse_shifts[nurse_id]['OFF'] = nurse_shifts[nurse_id].get('OFF', 0) + 1
                    last_5_shifts[nurse_id] = last_5_shifts.get(nurse_id, [])[-4:] + ['OFF']
                    return 'OFF'
            
            # 각 근무 유형별 가능성 평가
            shift_candidates = []
            
            # 최대 근무 횟수 제한 추가
            max_shifts_allowed = {
                'D': 10,  # 데이 근무 최대 10회로 제한
                'E': 10,  # 이브닝 근무 최대 10회로 제한
                'N': 10,  # 나이트 근무 최대 10회로 제한
            }
            
            # 각 근무 유형에 대해 점수 계산
            for shift in ['D', 'E', 'N', 'OFF']:
                # 휴가 요청이 있으면 무조건 OFF 배정
                if (nurse_id, date) in off_requests:
                    if shift == 'OFF':
                        shift_candidates.append((100, shift))
                    continue
                
                # OFF가 아닌 근무의 경우, 필요한 인원이 이미 충족되었는지 확인
                if shift != 'OFF' and daily_shift_requirements[date][shift] <= 0:
                    continue
                
                # 기본 점수 시작
                score = 0
                
                # 근무 선호도 점수 추가
                nurse_preference = nurse_preferences.get(nurse_id, {}).get(shift, 0)
                score += nurse_preference * 5  # 선호도를 점수에 반영
                
                # D와 E 근무 균형 강화 - 두 근무 유형 간의 차이 비교
                if not nurse.is_night_keeper and shift in ['D', 'E']:
                    d_count = nurse_shifts[nurse_id].get('D', 0)
                    e_count = nurse_shifts[nurse_id].get('E', 0)
                    
                    # D와 E 근무 간 차이가 크면 적은 쪽 선호
                    diff = abs(d_count - e_count)
                    if diff > 2:  # 차이가 2 이상이면 균형 조정
                        if (shift == 'D' and d_count < e_count) or (shift == 'E' and e_count < d_count):
                            # 적은 쪽에 가산점
                            score += 35 + (diff * 5)  # 차이가 클수록 더 높은 점수
                        else:
                            # 많은 쪽에 감점
                            score -= 30 + (diff * 5)  # 차이가 클수록 더 높은 패널티
                
                # 균형 점수 추가 - 각 근무 유형의 비율 고려 (가중치 증가)
                total_shifts = sum(nurse_shifts[nurse_id].values()) + 1  # 이번 근무를 포함
                current_ratio = (nurse_shifts[nurse_id].get(shift, 0) + 1) / total_shifts
                
                # 근무 유형별 이상적인 비율 (조정)
                ideal_ratios = {'D': 0.3, 'E': 0.3, 'N': 0.15, 'OFF': 0.25}  # D와 E를 동일하게 조정
                
                # 이상적인 비율에 가까울수록 높은 점수 (가중치 증가)
                ratio_score = 40 - abs(current_ratio - ideal_ratios[shift]) * 150
                score += ratio_score
                
                # 최대 근무 횟수 제한 적용
                if shift in max_shifts_allowed and nurse_shifts[nurse_id].get(shift, 0) >= max_shifts_allowed[shift]:
                    score -= 100  # 최대 근무 횟수 초과 시 큰 패널티
                
                # 아직 한 번도 배정되지 않은 근무 유형에 대해 우선 배정
                if shift != 'OFF' and nurse_shifts[nurse_id].get(shift, 0) == 0:
                    score += 30  # 아직 배정되지 않은 근무 유형에 높은 점수 부여
                
                # 목표 근무 횟수와의 차이에 따른 보정 점수 추가
                if not nurse.is_night_keeper and shift in ['D', 'E', 'N'] and nurse_id in target_shifts_per_nurse:
                    target = target_shifts_per_nurse[nurse_id].get(shift, 0)
                    current = nurse_shifts[nurse_id].get(shift, 0)
                    
                    # 목표보다 적게 배정된 경우 점수 추가
                    if current < target:
                        # 목표와의 차이가 클수록 더 높은 점수 (최대 35점)
                        gap_score = min(35, 25 * (target - current) / target)
                        score += gap_score
                    # 목표보다 많이 배정된 경우 점수 감소
                    elif current > target:
                        # 초과 정도에 비례해 패널티 부여 (최대 50점)
                        excess_penalty = min(50, 40 * (current - target) / target)
                        score -= excess_penalty
                
                # 패턴 점수 추가
                pattern_score = calculate_pattern_score(nurse_id, date, shift, last_5_shifts)
                score += pattern_score
                
                # 특정 패턴 강화 - 자연스러운 로테이션 (D→E→N→OFF 순환 강화)
                recent_shifts = last_5_shifts.get(nurse_id, [])
                if recent_shifts:
                    last_shift = recent_shifts[-1]
                    # 주/저녁/야간/휴무의 순환 패턴 강화
                    natural_rotation = {
                        'D': 'E',  # 주간근무 후 저녁근무 선호
                        'E': 'N',  # 저녁근무 후 야간근무 선호
                        'N': 'OFF',  # 야간근무 후 휴무 강력 선호
                        'OFF': 'D'   # 휴무 후 주간근무로 시작 선호
                    }
                    
                    # 자연스러운 순환이면 추가 점수 (대폭 증가)
                    if natural_rotation.get(last_shift) == shift:
                        score += 50  # 순환 근무 패턴 강화를 위해 점수 증가
                    # 역방향 순환에는 패널티
                    elif natural_rotation.get(shift) == last_shift:
                        score -= 40  # 역방향 순환에 패널티
                
                # 연속 근무/휴무 제한
                consecutive_count = 0
                for s in reversed(recent_shifts):
                    if s == shift:
                        consecutive_count += 1
                    else:
                        break
                
                # 연속 제한 - 같은 근무 타입이 너무 많이 연속되지 않도록
                max_consecutive = {
                    'D': 3,  # 최대 3일 연속 주간 근무 (감소)
                    'E': 3,  # 최대 3일 연속 저녁 근무 (감소)
                    'N': 2,  # 최대 2일 연속 야간 근무
                    'OFF': 2  # 최대 2일 연속 휴무
                }
                
                if consecutive_count >= max_consecutive[shift]:
                    score -= 45  # 연속 제한 초과 시 강한 패널티
                
                # 후보 리스트에 추가
                shift_candidates.append((score, shift))
            
            # 점수로 정렬
            shift_candidates.sort(reverse=True)
            
            # 최적의 근무 선택
            if not shift_candidates:
                return 'OFF'  # 가능한 근무가 없으면 OFF 반환
            
            best_score, best_shift = shift_candidates[0]
            
            # 최종 일정에 추가
            final_schedule[(nurse_id, date)] = best_shift
            
            # 간호사별 근무 카운트 업데이트
            nurse_shifts[nurse_id][best_shift] = nurse_shifts[nurse_id].get(best_shift, 0) + 1
            
            # 일일 필요 인원 업데이트
            if best_shift != 'OFF':
                daily_shift_requirements[date][best_shift] -= 1
            
            # 마지막 5개 근무 기록 업데이트
            last_5_shifts[nurse_id] = last_5_shifts.get(nurse_id, [])[-4:] + [best_shift]
            
            # 스킬 요구사항 업데이트
            update_skill_requirements(nurse_id, date, best_shift)
            
            return best_shift
        
        # 스케줄 생성 완료 후 숙련도 1 간호사에 대한 추가 교육 배정
        for date in date_range:
            for shift_type in ['D', 'E', 'N']:
                # 해당 shift_type에 대해 이미 배정된 간호사 숫자 확인
                assigned_count = sum(1 for (n_id, d), s in final_schedule.items() if d == date and s == shift_type)
                required_count = daily_shift_requirements[date][shift_type]
                
                # 해당 근무에 숙련도 1인 간호사 중 아직 배정되지 않은 간호사 찾기
                if assigned_count >= required_count:  # 필요 인원이 이미 채워진 경우만 추가 교육 고려
                    trainee_candidates = []
                    for nurse in nurse_list:
                        if nurse.skill_level == 1 and not nurse.is_night_keeper:
                            # 해당 날짜에 배정되지 않았는지 확인
                            if (nurse.id, date) not in final_schedule:
                                # 유효한 배정인지 확인
                                if is_valid_assignment(nurse.id, date, shift_type):
                                    trainee_candidates.append(nurse.id)
                    
                    # 후보자 중 한 명을 교육용으로 추가 배정
                    if trainee_candidates:
                        try:
                            trainee_id = random.choice(trainee_candidates)
                            final_schedule[(trainee_id, date)] = shift_type
                            
                            # nurse_shift_counts 초기화 확인 및 안전한 업데이트
                            if trainee_id not in nurse_shift_counts:
                                nurse_shift_counts[trainee_id] = {'D': 0, 'E': 0, 'N': 0}
                            
                            nurse_shift_counts[trainee_id][shift_type] += 1
                            # 추가 교육 목적으로 배정되었음을 메타데이터로 표시할 수 있음
                        except Exception as e:
                            # 오류 발생 시 로그 출력
                            print(f"Error in trainee assignment: {e}, trainee_id={trainee_id if 'trainee_id' in locals() else 'unknown'}")
                            continue
        
        # 특별한 날짜에 대한 처리 (휴일 등)
        for day in date_range:
            if is_holiday(day):
                # 휴일/주말에는 필요 인원 그대로 유지
                pass
        
        # 숙련도별 요구사항 업데이트 함수는 이미 위에서 정의되어 있으므로 제거합니다.
        # def update_skill_requirements(nurse, day, shift):
        #     """간호사의 숙련도 기반으로 해당 일자/교대의 숙련도 요구사항 업데이트"""
        #     skill = nurse.skill_level
        #     
        #     # 숙련도 범주 판단
        #     if skill >= 5:  # 고급: 5-6
        #         skill_category = 'high'
        #     elif skill >= 3:  # 중급: 3-4
        #         skill_category = 'mid'
        #     else:  # 초급: 1-2
        #         skill_category = 'low'
        #         
        #     # 해당 숙련도 범주의 필요 인원이 있으면 감소시킴
        #     if skill_requirements[day][shift][skill_category] > 0:
        #         skill_requirements[day][shift][skill_category] -= 1
        #         return True  # 요구사항이 감소되었음을 의미
        #     else:
        #         # 숙련도 1인 경우는 추가 배정 가능
        #         if skill == 1:
        #             return True  # 숙련도 1은 추가 배정 가능
        #         
        #         # 다른 숙련도 범주의 요구사항이 아직 남아있는지 확인
        #         if any(skill_requirements[day][shift][cat] > 0 for cat in ['high', 'mid', 'low']):
        #             return False  # 다른 숙련도 범주의 요구사항이 남아있어 이 간호사 배정 불가
        #         
        #         # 모든 숙련도 범주의 필요 인원이 충족된 경우, 추가 인원으로 배정 가능
        #         return True
        
        # 최종 스케줄 결과는 이미 위에서 선언되어 있으므로 제거합니다.
        # final_schedule = {}
        
        # 간호사별 이전 근무 타입 추적
        nurse_previous_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 간호사의 마지막 5개 근무 추적
        last_5_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 백업용 변수 초기화
        backup_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 간호사별 근무 타입 카운트 초기화
        nurse_shift_counts = {nurse.id: {'D': 0, 'E': 0, 'N': 0} for nurse in nurse_list}
        
        # 각 간호사별 총 남은 근무일수 초기화
        remaining_shifts_per_nurse = {nurse.id: len(date_range) for nurse in nurse_list}
        
        # 각 간호사별 남은 N 근무일수 초기화 (나이트 근무 특별 관리)
        n_shifts_available = {nurse.id: (len(date_range) // 2 if not nurse.is_night_keeper else len(date_range)) for nurse in nurse_list}
        
        # 간호사의 선호 근무 유형 초기화 (기본값: D, E, N 순으로 선호)
        nurse_preferred_shifts = {nurse.id: ['D', 'E', 'N'] for nurse in nurse_list}
        
        # 각 간호사별 최대 배정 가능 근무 수 계산
        max_shifts_per_nurse = total_days - (total_days // 3)  # 약 2/3는 근무, 1/3은 OFF로 가정
        
        # 나이트 킵 간호사는 N을 가장 선호하도록 설정
        for nurse in nurse_list:
            if nurse.is_night_keeper:
                nurse_preferred_shifts[nurse.id] = ['N', 'OFF', 'OFF']
        
        # 나이트 킵 간호사 먼저 배정 - 매일 N 근무 우선 배정
        night_keepers = [nurse for nurse in nurse_list if nurse.is_night_keeper]
        if night_keepers:
            messages.info(request, f'나이트 킵 간호사 {len(night_keepers)}명을 먼저 N 근무에 배정합니다.')
            
            # 일자별로 순회하며 나이트 킵 간호사에게 N 근무 배정
            day_idx = 0
            while day_idx < len(date_range):
                try:
                    # 연속 2일 N 근무 패턴(NN)을 우선적으로 시도
                    if day_idx + 1 < len(date_range):  # 다음 날이 범위 내에 있는지 확인
                        # 오늘과 내일의 필요 N 근무 인원 확인 - 항상 4명으로 고정
                        required_n_today = min(daily_shift_requirements[date_range[day_idx]]['N'], 4)
                        required_n_tomorrow = min(daily_shift_requirements[date_range[day_idx + 1]]['N'], 4)
                        
                        # 나이트 킵 간호사에게 연속 2일 N 근무 배정 시도
                        for nurse in night_keepers:
                            # 연속 2일(NN) 모두 배정할 근무가 남아있고, 필요 인원이 아직 미달인 경우에만 배정
                            if (nurse_shifts[nurse.id] >= 2 and 
                                required_n_today > 0 and required_n_tomorrow > 0):
                                
                                # 숙련도 요구사항 확인
                                today_skill_ok = update_skill_requirements(nurse.id, date_range[day_idx], 'N')
                                tomorrow_skill_ok = update_skill_requirements(nurse.id, date_range[day_idx + 1], 'N')
                                
                                # 숙련도 요구사항이 충족되지 않으면 다음 간호사로 넘어감
                                if not (today_skill_ok and tomorrow_skill_ok):
                                    continue
                                
                                # 오늘 N 근무 배정
                                final_schedule[(nurse.id, date_range[day_idx])] = 'N'
                                nurse_shifts[nurse.id] -= 1
                                daily_shift_requirements[date_range[day_idx]]['N'] -= 1
                                required_n_today -= 1  # 현재 날짜 필요 인원 감소
                                
                                # 내일 N 근무 배정
                                final_schedule[(nurse.id, date_range[day_idx + 1])] = 'N'
                                nurse_shifts[nurse.id] -= 1
                                daily_shift_requirements[date_range[day_idx + 1]]['N'] -= 1
                                required_n_tomorrow -= 1  # 다음 날짜 필요 인원 감소
                                
                                # 이전 근무 타입 기록 업데이트
                                # 초기화 확인 추가
                                if nurse.id not in nurse_previous_shifts:
                                    nurse_previous_shifts[nurse.id] = []
                                
                                nurse_previous_shifts[nurse.id].append('N')
                                nurse_previous_shifts[nurse.id].append('N')
                                if len(nurse_previous_shifts[nurse.id]) > 5:  # 최근 5일만 기록
                                    nurse_previous_shifts[nurse.id] = nurse_previous_shifts[nurse.id][-5:]
                                
                                # 2일 연속 N 근무 후에는 반드시 2일의 OFF 보장
                                # 다음 2일이 범위 내에 있는지 확인하고 OFF 배정
                                for off_day in range(2, 4):  # 다음 2일(idx+2, idx+3) 만 OFF 배정
                                    if day_idx + off_day < len(date_range):
                                        final_schedule[(nurse.id, date_range[day_idx + off_day])] = 'OFF'
                                        # OFF는 남은 근무수에서 차감하지 않음
                                        # nurse_previous_shifts 업데이트
                                        if nurse.id not in nurse_previous_shifts:
                                            nurse_previous_shifts[nurse.id] = []
                                        
                                        nurse_previous_shifts[nurse.id].append('OFF')
                                        if len(nurse_previous_shifts[nurse.id]) > 5:
                                            nurse_previous_shifts[nurse.id] = nurse_previous_shifts[nurse.id][-5:]
                        
                        # 연속 N 근무 배정 후 날짜 인덱스 업데이트 (2일 N + 2일 OFF = 총 4일)
                        day_idx += 4
                        continue
                    
                    # 연속 N 배정이 안되면 단일 N 근무 배정 시도하지 않고 그냥 다음 날짜로 넘어감
                    day_idx += 1
                except Exception as e:
                    # 오류 발생 시 로그 출력하고 계속 진행
                    print(f"Error in night keeper assignment: {e}, day_idx={day_idx}")
                    day_idx += 1  # 오류 발생한 날짜는 건너뜀
                    continue
        
        # 3. 제약 조건 정의
        def is_valid_assignment(nurse_id, date, shift):
            """주어진 간호사, 날짜, 근무가 유효한지 검사하는 함수"""
            nonlocal final_schedule, skill_requirements, daily_shift_requirements
            
            # 이전/이후 날짜 계산
            prev_date = date - timedelta(days=1)
            prev_2_date = date - timedelta(days=2)
            next_date = date + timedelta(days=1)
            
            # 간호사 정보 가져오기
            nurse = next((n for n in nurse_list if n.id == nurse_id), None)
            if not nurse:
                return False
                
            # 1. 이미 근무가 배정되어 있으면 변경 불가
            if (nurse_id, date) in final_schedule:
                return False
            
            # 2. 나이트 킵 간호사는 N 근무 또는 OFF만 배정 가능
            if nurse.is_night_keeper and shift != 'N' and shift != 'OFF':
                return False
            
            # 3. 일주일 단위로 6일 이상 근무할 수 없다
            if shift != 'OFF':
                # 현재 주의 시작일 계산 (월요일 기준)
                week_start = date - timedelta(days=date.weekday())
                week_end = week_start + timedelta(days=6)
                
                # 같은 주 내 이미 배정된 근무일 수
                week_work_days = 0
                current_day = week_start
                while current_day <= week_end:
                    # 이전에 배정된 근무 확인
                    if current_day < date and (nurse_id, current_day) in final_schedule and final_schedule[(nurse_id, current_day)] != 'OFF':
                        week_work_days += 1
                    # 오늘 근무가 추가되면 +1
                    elif current_day == date and shift != 'OFF':
                        week_work_days += 1
                    current_day += timedelta(days=1)
                
                # 이번 주에 6일 이상 근무하면 불가
                if week_work_days >= 6:
                    return False
            
            # 4. E근무 이후에는 D근무가 올 수 없다 (순환근무 패턴 강화)
            if shift == 'D' and (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'E':
                return False
            
            # 5. N근무 이후에는 N근무 또는 2일 OFF여야 한다
            if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                # N 다음에는 N 또는 OFF만 가능
                if shift != 'N' and shift != 'OFF':
                    return False
                
                # N 다음에 OFF면, 그 다음날도 OFF여야 함
                if shift == 'OFF' and next_date <= end_date:
                    # 다음날이 범위 내에 있고 이미 배정되었으면 OFF여야 함
                    if (nurse_id, next_date) in final_schedule and final_schedule[(nurse_id, next_date)] != 'OFF':
                        return False
            
            # 6. 나이트킵 간호사는 NN 근무 또는 NNN 근무만 적용
            if nurse.is_night_keeper and shift == 'N':
                # N 근무를 시작할 때는 연속 2일 또는 3일 N을 보장해야 함
                consecutive_n = 1  # 오늘 배정될 N 포함
                
                # 이전에 배정된 N 확인
                if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                    consecutive_n += 1
                    if (nurse_id, prev_2_date) in final_schedule and final_schedule[(nurse_id, prev_2_date)] == 'N':
                        consecutive_n += 1
                
                # 나이트킵 간호사가 단일 N을 시작하려면, 연속 2-3일 보장 필요
                if consecutive_n == 1:  # 오늘부터 N 시작이면
                    next_day = date + timedelta(days=1)
                    next_2_day = date + timedelta(days=2)
                    
                    # 다음날이 범위 내이고 이미 OFF 외 다른 근무로 배정되었으면 N 배정 불가
                    if next_day <= end_date and (nurse_id, next_day) in final_schedule and final_schedule[(nurse_id, next_day)] != 'N':
                        return False
                        
                    # 최소 NN 패턴 필요, NNN도 가능
                    if next_day > end_date:  # 다음날이 범위를 넘어가면 N 배정 불가
                        return False
            
            # 7. 숙련도에 따른 근무 배정 밸런스
            if shift != 'OFF':
                # 숙련도 범주 결정 (1-2: 초급, 3-4: 중급, 5-6: 고급)
                if nurse.skill_level >= 5:
                    skill_category = 'high'
                elif nurse.skill_level >= 3:
                    skill_category = 'mid'
                else:
                    skill_category = 'low'
                
                # 해당 숙련도 범주가 아직 필요한지 확인
                if skill_requirements[date][shift][skill_category] <= 0:
                    # 이미 해당 숙련도 범주의 필요 인원이 충족된 경우
                    
                    # 총 필요 인원을 계산
                    total_required = daily_shift_requirements[date][shift]
                    total_skill_required = (
                        skill_requirements[date][shift]['high'] + 
                        skill_requirements[date][shift]['mid'] + 
                        skill_requirements[date][shift]['low']
                    )
                    
                    # 아직 총 필요 인원이 남아있지 않다면 배정 불가
                    if total_required <= total_skill_required:
                        return False
            
            # 8. 근무 필요 인원 설정에 따라 일일 근무수가 맞춰져야 함
            if shift != 'OFF':
                # 이미 해당 근무 유형에 필요한 인원이 모두 배정되었는지 확인
                if daily_shift_requirements[date][shift] <= 0:
                    return False
                    
            return True

        # 최종 스케줄 결과는 이미 위에서 선언되어 있으므로 제거합니다.
        # final_schedule = {}
        
        # 간호사별 이전 근무 타입 추적
        nurse_previous_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 간호사의 마지막 5개 근무 추적
        last_5_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 백업용 변수 초기화
        backup_shifts = {nurse.id: [] for nurse in nurse_list}
        
        # 간호사별 근무 타입 카운트 초기화
        nurse_shift_counts = {nurse.id: {'D': 0, 'E': 0, 'N': 0} for nurse in nurse_list}
        
        # 각 간호사별 총 남은 근무일수 초기화
        remaining_shifts_per_nurse = {nurse.id: len(date_range) for nurse in nurse_list}
        
        # 각 간호사별 남은 N 근무일수 초기화 (나이트 근무 특별 관리)
        n_shifts_available = {nurse.id: (len(date_range) // 2 if not nurse.is_night_keeper else len(date_range)) for nurse in nurse_list}
        
        # 간호사의 선호 근무 유형 초기화 (기본값: D, E, N 순으로 선호)
        nurse_preferred_shifts = {nurse.id: ['D', 'E', 'N'] for nurse in nurse_list}
        
        # 각 간호사별 최대 배정 가능 근무 수 계산
        max_shifts_per_nurse = total_days - (total_days // 3)  # 약 2/3는 근무, 1/3은 OFF로 가정
        
        # 나이트 킵 간호사는 N을 가장 선호하도록 설정
        for nurse in nurse_list:
            if nurse.is_night_keeper:
                nurse_preferred_shifts[nurse.id] = ['N', 'OFF', 'OFF']
        
        # 나이트 킵 간호사 먼저 배정 - 매일 N 근무 우선 배정
        night_keepers = [nurse for nurse in nurse_list if nurse.is_night_keeper]
        if night_keepers:
            messages.info(request, f'나이트 킵 간호사 {len(night_keepers)}명을 먼저 N 근무에 배정합니다.')
            
            # 일자별로 순회하며 나이트 킵 간호사에게 N 근무 배정
            day_idx = 0
            while day_idx < len(date_range):
                try:
                    # 연속 2일 N 근무 패턴(NN)을 우선적으로 시도
                    if day_idx + 1 < len(date_range):  # 다음 날이 범위 내에 있는지 확인
                        # 오늘과 내일의 필요 N 근무 인원 확인 - 항상 4명으로 고정
                        required_n_today = min(daily_shift_requirements[date_range[day_idx]]['N'], 4)
                        required_n_tomorrow = min(daily_shift_requirements[date_range[day_idx + 1]]['N'], 4)
                        
                        # 나이트 킵 간호사에게 연속 2일 N 근무 배정 시도
                        for nurse in night_keepers:
                            # 연속 2일(NN) 모두 배정할 근무가 남아있고, 필요 인원이 아직 미달인 경우에만 배정
                            if (nurse_shifts[nurse.id] >= 2 and 
                                required_n_today > 0 and required_n_tomorrow > 0):
                                
                                # 숙련도 요구사항 확인
                                today_skill_ok = update_skill_requirements(nurse.id, date_range[day_idx], 'N')
                                tomorrow_skill_ok = update_skill_requirements(nurse.id, date_range[day_idx + 1], 'N')
                                
                                # 숙련도 요구사항이 충족되지 않으면 다음 간호사로 넘어감
                                if not (today_skill_ok and tomorrow_skill_ok):
                                    continue
                                
                                # 오늘 N 근무 배정
                                final_schedule[(nurse.id, date_range[day_idx])] = 'N'
                                nurse_shifts[nurse.id] -= 1
                                daily_shift_requirements[date_range[day_idx]]['N'] -= 1
                                required_n_today -= 1  # 현재 날짜 필요 인원 감소
                                
                                # 내일 N 근무 배정
                                final_schedule[(nurse.id, date_range[day_idx + 1])] = 'N'
                                nurse_shifts[nurse.id] -= 1
                                daily_shift_requirements[date_range[day_idx + 1]]['N'] -= 1
                                required_n_tomorrow -= 1  # 다음 날짜 필요 인원 감소
                                
                                # 이전 근무 타입 기록 업데이트
                                # 초기화 확인 추가
                                if nurse.id not in nurse_previous_shifts:
                                    nurse_previous_shifts[nurse.id] = []
                                
                                nurse_previous_shifts[nurse.id].append('N')
                                nurse_previous_shifts[nurse.id].append('N')
                                if len(nurse_previous_shifts[nurse.id]) > 5:  # 최근 5일만 기록
                                    nurse_previous_shifts[nurse.id] = nurse_previous_shifts[nurse.id][-5:]
                                
                                # 2일 연속 N 근무 후에는 반드시 2일의 OFF 보장
                                # 다음 2일이 범위 내에 있는지 확인하고 OFF 배정
                                for off_day in range(2, 4):  # 다음 2일(idx+2, idx+3) 만 OFF 배정
                                    if day_idx + off_day < len(date_range):
                                        final_schedule[(nurse.id, date_range[day_idx + off_day])] = 'OFF'
                                        # OFF는 남은 근무수에서 차감하지 않음
                                        # nurse_previous_shifts 업데이트
                                        if nurse.id not in nurse_previous_shifts:
                                            nurse_previous_shifts[nurse.id] = []
                                        
                                        nurse_previous_shifts[nurse.id].append('OFF')
                                        if len(nurse_previous_shifts[nurse.id]) > 5:
                                            nurse_previous_shifts[nurse.id] = nurse_previous_shifts[nurse.id][-5:]
                        
                        # 연속 N 근무 배정 후 날짜 인덱스 업데이트 (2일 N + 2일 OFF = 총 4일)
                        day_idx += 4
                        continue
                    
                    # 연속 N 배정이 안되면 단일 N 근무 배정 시도하지 않고 그냥 다음 날짜로 넘어감
                    day_idx += 1
                except Exception as e:
                    # 오류 발생 시 로그 출력하고 계속 진행
                    print(f"Error in night keeper assignment: {e}, day_idx={day_idx}")
                    day_idx += 1  # 오류 발생한 날짜는 건너뜀
                    continue
        
        # 5. 나머지 날짜는 OFF로 채우기
        for day in date_range:
            for nurse in nurse_list:
                if (nurse.id, day) not in final_schedule:
                    final_schedule[(nurse.id, day)] = 'OFF'
        
        # 6. 균형 상태 체크 및 추가 조정
        # 간호사별 최종 근무 유형 카운트 계산
        nurse_final_counts = {nurse.id: {'D': 0, 'E': 0, 'N': 0} for nurse in nurse_list}
        for (nurse_id, date), shift in final_schedule.items():
            if shift in ['D', 'E', 'N']:
                nurse_final_counts[nurse_id][shift] += 1
        
        # 균형 조정이 필요한 간호사 목록 구성 
        unbalanced_nurses = []
        for nurse_id, counts in nurse_final_counts.items():
            total = sum(counts.values())
            if total > 0:
                avg = total / 3
                max_diff = max([abs(counts['D'] - avg), abs(counts['E'] - avg), abs(counts['N'] - avg)])
                # 차이가 2 이상인 경우 균형 조정 필요
                unbalanced_nurses.append((nurse_id, counts, max_diff))
        
        # 편차가 큰 순서대로 정렬
        unbalanced_nurses.sort(key=lambda x: x[2], reverse=True)
        
        # 균형 조정 최대 시도 횟수 설정
        max_balance_attempts = 50
        balance_attempts = 0
        
        # 균형이 맞지 않는 간호사들에 대해 근무 유형 교환 시도 - 강화된 버전
        while unbalanced_nurses and balance_attempts < max_balance_attempts:
            nurse_id, counts, max_diff = unbalanced_nurses[0]
            balance_attempts += 1
            
            if max_diff < 1.0:  # 1.0 미만의 편차는 허용
                break
                
            # 가장 많이 배정받은 유형과 가장 적게 배정받은 유형 파악
            max_shift = max(counts, key=counts.get)
            min_shift = min(counts, key=counts.get)
            
            # 교체 성공 여부
            swap_success = False
            
            # 해당 간호사의 max_shift 근무 중 하나를 min_shift로 교체 시도
            for day in date_range:
                if (nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] == max_shift:
                    # 해당 날짜에 min_shift 배정이 가능한지 확인
                    temp_schedule = copy.deepcopy(final_schedule)
                    del temp_schedule[(nurse_id, day)]
                    
                    # 교체 가능한지 확인
                    if is_valid_assignment(nurse_id, day, min_shift):
                        # 교체 수행
                        final_schedule[(nurse_id, day)] = min_shift
                        counts[max_shift] -= 1
                        counts[min_shift] += 1
                        
                        # 변경 성공
                        swap_success = True
                        break
            
            # 다른 간호사와의 교환 시도
            if not swap_success:
                for other_nurse in nurse_list:
                    other_id = other_nurse.id
                    if other_id == nurse_id:
                        continue
                        
                    other_counts = nurse_final_counts[other_id]
                    
                    # 다른 간호사의 min_shift와 이 간호사의 max_shift 교환
                    for day in date_range:
                        if ((nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] == max_shift and
                            (other_id, day) in final_schedule and final_schedule[(other_id, day)] == min_shift):
                            
                            # 교환 전에 유효성 검사
                            temp_schedule = copy.deepcopy(final_schedule)
                            temp_schedule[(nurse_id, day)] = min_shift
                            temp_schedule[(other_id, day)] = max_shift
                            
                            valid_for_nurse = True
                            valid_for_other = True
                            
                            # 교환이 제약조건을 위반하는지 검사
                            for check_day in [day-timedelta(days=1), day+timedelta(days=1)]:
                                if valid_for_nurse and (nurse_id, check_day) in final_schedule:
                                    check_shift = min_shift
                                    prev_or_next_shift = final_schedule[(nurse_id, check_day)]
                                    
                                    # 연속 근무 제약 위반 여부 확인
                                    if ((check_shift == 'N' and prev_or_next_shift == 'E') or
                                        (check_shift == 'D' and prev_or_next_shift == 'N') or
                                        (check_shift == 'D' and prev_or_next_shift == 'E') or
                                        # 추가된 제약 조건
                                        (check_shift == 'E' and prev_or_next_shift == 'N') or  # E 근무 앞에 N 근무 금지
                                        (check_shift == 'D' and prev_or_next_shift == 'N') or  # D 근무 앞에 N 근무 금지
                                        (check_shift == 'E' and prev_or_next_shift == 'E')):   # E 근무 앞에 E 근무 금지
                                        valid_for_nurse = False
                                
                                if valid_for_other and (other_id, check_day) in final_schedule:
                                    check_shift = max_shift
                                    prev_or_next_shift = final_schedule[(other_id, check_day)]
                                    
                                    # 연속 근무 제약 위반 여부 확인
                                    if ((check_shift == 'N' and prev_or_next_shift == 'E') or
                                        (check_shift == 'D' and prev_or_next_shift == 'N') or
                                        (check_shift == 'D' and prev_or_next_shift == 'E') or
                                        # 추가된 제약 조건
                                        (check_shift == 'E' and prev_or_next_shift == 'N') or  # E 근무 앞에 N 근무 금지
                                        (check_shift == 'D' and prev_or_next_shift == 'N') or  # D 근무 앞에 N 근무 금지
                                        (check_shift == 'E' and prev_or_next_shift == 'E')):   # E 근무 앞에 E 근무 금지
                                        valid_for_other = False
                            
                            # OFF N OFF 패턴 검사 추가
                            # nurse_id에 대한 OFF N OFF 패턴 검사
                            if valid_for_nurse and min_shift == 'N':
                                prev_date = day - timedelta(days=1)
                                next_date = day + timedelta(days=1)
                                next_2_date = day + timedelta(days=2)
                                
                                # 이전 날이 OFF인지 확인
                                prev_is_off = (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'OFF'
                                
                                # 다음 날이 OFF인지 확인
                                next_is_off = (nurse_id, next_date) in final_schedule and final_schedule[(nurse_id, next_date)] == 'OFF'
                                
                                # OFF N OFF 패턴 방지 - 이전이 OFF이고 다음이 OFF면 교환 불가
                                if prev_is_off and next_is_off:
                                    valid_for_nurse = False
                                
                                # N 근무는 항상 연속으로 최소 2개 이상 - OFF N N 패턴 강제
                                if prev_is_off:
                                    # 다음날이 N이 아니면서 범위 내에 있으면 교환 불가
                                    if (nurse_id, next_date) in final_schedule and final_schedule[(nurse_id, next_date)] != 'N':
                                        valid_for_nurse = False
                                    # 아직 다음날 스케줄이 결정되지 않았지만 그 다음날이 OFF면 교환 불가
                                    elif (nurse_id, next_date) not in final_schedule and (nurse_id, next_2_date) in final_schedule and final_schedule[(nurse_id, next_2_date)] == 'OFF':
                                        valid_for_nurse = False
                            
                            
                            # other_id에 대한 OFF N OFF 패턴 검사
                            if valid_for_other and max_shift == 'N':
                                prev_date = day - timedelta(days=1)
                                next_date = day + timedelta(days=1)
                                
                                prev_is_off = (other_id, prev_date) in final_schedule and final_schedule[(other_id, prev_date)] == 'OFF'
                                next_is_off = (other_id, next_date) in final_schedule and final_schedule[(other_id, next_date)] == 'OFF'
                                
                                if prev_is_off and next_is_off:
                                    valid_for_other = False
                            
                            # E 다음에 D가 오는 패턴 추가 검사
                            if valid_for_nurse and min_shift == 'D':
                                prev_date = day - timedelta(days=1)
                                if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'E':
                                    valid_for_nurse = False
                            
                            if valid_for_other and max_shift == 'D':
                                prev_date = day - timedelta(days=1)
                                if (other_id, prev_date) in final_schedule and final_schedule[(other_id, prev_date)] == 'E':
                                    valid_for_other = False
                            
                            if valid_for_nurse and valid_for_other:
                                # 교환 수행
                                final_schedule[(nurse_id, day)] = min_shift
                                final_schedule[(other_id, day)] = max_shift
                                
                                # 카운트 업데이트
                                counts[max_shift] -= 1
                                counts[min_shift] += 1
                                other_counts[max_shift] += 1
                                other_counts[min_shift] -= 1
                                
                                swap_success = True
                                break
                    
                    if swap_success:
                        break
            
            # 새 편차 계산
            if swap_success:
                total = sum(counts.values())
                if total > 0:
                    avg = total / 3
                    max_diff = max([abs(counts['D'] - avg), abs(counts['E'] - avg), abs(counts['N'] - avg)])
                    
                    # 균형이 개선되었으면 리스트 업데이트
                    unbalanced_nurses[0] = (nurse_id, counts, max_diff)
                    # 편차 기준으로 정렬
                    unbalanced_nurses.sort(key=lambda x: x[2], reverse=True)
            else:
                # 교체 시도가 모두 실패하면 다음 간호사로 넘어감
                unbalanced_nurses.pop(0)
                if unbalanced_nurses:  # 다음 간호사 있는 경우
                    unbalanced_nurses.sort(key=lambda x: x[2], reverse=True)
        
        # 균형 조정 후에 다시 각 날짜별로 필요 인원수 확인 및 추가 배정
        messages.info(request, "균형 조정 후 인원수 검증 및 추가 배정을 시작합니다.")
        
        # 정보 수집: 각 날짜/근무별 인원 부족 현황
        staffing_shortages = []
        for day in date_range:
            for shift_type in ['D', 'E', 'N']:
                required = daily_shift_requirements[day][shift_type]
                
                # 현재 배정된 수 확인
                assigned_count = sum(1 for (n_id, d), s in final_schedule.items() 
                                  if d == day and s == shift_type)
                
                if assigned_count < required:
                    shortage = required - assigned_count
                    staffing_shortages.append((day, shift_type, shortage))
        
        # 인원 부족이 심각한 순서로 정렬
        staffing_shortages.sort(key=lambda x: x[2], reverse=True)
        
        # 크게 부족한 날짜부터 추가 배정 시도
        for day, shift_type, shortage in staffing_shortages:
            messages.warning(request, f'{day.strftime("%Y-%m-%d")}에 {shift_type} 근무가 {shortage}명 부족합니다. 추가 배정을 시도합니다.')
            
            # 이미 해당 날짜에 배정된 간호사 목록
            assigned_nurses_today = [n_id for (n_id, d), s in final_schedule.items() if d == day]
            
            # 배정 시도할 간호사 후보군
            candidates = []
            relaxed_candidates = []
            extremely_relaxed_candidates = []  # 매우 완화된 제약 조건으로 배정 가능한 간호사
            
            for nurse in nurse_list:
                nurse_id = nurse.id
                
                # 이미 오늘 배정된 간호사는 건너뛰기
                if nurse_id in assigned_nurses_today:
                    continue
                
                # 기본 검증 - 전체 제약 조건 확인
                if remaining_shifts_per_nurse[nurse_id] > 0 and is_valid_assignment(nurse_id, day, shift_type):
                    # 기본 점수 계산 - 균형 점수, 패턴 점수 등
                    d_count = nurse_shift_counts[nurse_id]['D']
                    e_count = nurse_shift_counts[nurse_id]['E']
                    n_count = nurse_shift_counts[nurse_id]['N']
                    
                    total = sum([d_count, e_count, n_count])
                    
                    # 해당 근무 유형의 비율
                    current_count = nurse_shift_counts[nurse_id][shift_type]
                    shift_ratio = current_count / (total + 1)  # +1로 나누기 0 방지
                    
                    # 해당 타입 근무가 적으면 가산점
                    if shift_type == 'D':
                        balance_score = 100 * (0.4 - shift_ratio) if shift_ratio < 0.4 else 0
                    elif shift_type == 'E':
                        balance_score = 100 * (0.3 - shift_ratio) if shift_ratio < 0.3 else 0
                    elif shift_type == 'N':
                        balance_score = 100 * (0.33 - shift_ratio) if shift_ratio < 0.33 else 0
                    else:
                        balance_score = 50  # 아직 근무가 없으면 중간 점수
                    
                    # 근무 패턴 점수 - D→E→N 순환 패턴에 높은 점수
                    pattern_score = 0
                    
                    # D→E→N 순환 패턴 우선 (이전 날짜 확인)
                    prev_date = day - timedelta(days=1)
                    if (nurse_id, prev_date) in final_schedule:
                        prev_shift = final_schedule[(nurse_id, prev_date)]
                        
                        # D 다음 E 패턴 선호
                        if prev_shift == 'D' and shift_type == 'E':
                            pattern_score += 80
                        
                        # E 다음 N 패턴 선호
                        elif prev_shift == 'E' and shift_type == 'N':
                            pattern_score += 80
                        
                        # N 다음 OFF 다음에는 D 선호
                        elif prev_shift == 'OFF' and shift_type == 'D':
                            prev_2_date = day - timedelta(days=2)
                            if (nurse_id, prev_2_date) in final_schedule and final_schedule[(nurse_id, prev_2_date)] == 'N':
                                pattern_score += 80  # N→OFF→D 패턴에 높은 점수
                    
                    # 패턴 점수와 균형 점수를 합산하여 후보군에 추가
                    total_score = pattern_score + balance_score
                    candidates.append((total_score, nurse_id))
                
                # 완화된 검증 - 일부 선호 제약 조건 완화
                elif remaining_shifts_per_nurse[nurse_id] > 0:
                    # 제약 조건 완화 1 - 연속 근무 제한만 확인 (제약 조건 완화)
                    can_assign_relaxed = True
                    
                    # 나이트 킵 간호사는 N 근무만 배정 가능 (이 조건은 절대 완화하지 않음)
                    nurse = next((n for n in nurse_list if n.id == nurse_id), None)
                    if nurse and nurse.is_night_keeper and shift_type != 'N':
                        can_assign_relaxed = False
                    
                    # 기본 연속 근무 제약 조건만 확인 (3일 연속 근무 금지)
                    consecutive_work_days = 0
                    for i in range(1, 4):
                        prev_date = day - timedelta(days=i)
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] != 'OFF':
                            consecutive_work_days += 1
                        else:
                            break
                    
                    # 연속 근무 조건 완화 - 최대 5일까지 허용 (기본은 3일)
                    if consecutive_work_days >= 5:
                        can_assign_relaxed = False
                    
                    # N 근무 후 무조건 OFF 규칙만 준수 - 절대 완화하지 않음
                    prev_date = day - timedelta(days=1)
                    if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                        can_assign_relaxed = False
                    
                    # 2일 전이 N 근무이고 어제가 OFF면 오늘도 무조건 OFF만 가능 - 절대 완화하지 않음
                    prev_2_date = day - timedelta(days=2)
                    if ((nurse_id, prev_2_date) in final_schedule and final_schedule[(nurse_id, prev_2_date)] == 'N') and \
                       ((nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'OFF'):
                        can_assign_relaxed = False
                    
                    # E 근무 다음날에 D 근무 금지 규칙도 유지
                    if shift_type == 'D':
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'E':
                            can_assign_relaxed = False
                    
                    # 가능하면 후보자 리스트에 추가
                    if can_assign_relaxed:
                        # 균형 점수 계산 (해당 타입의 근무가 적은 간호사 선호)
                        d_count = nurse_shift_counts[nurse_id]['D']
                        e_count = nurse_shift_counts[nurse_id]['E']
                        n_count = nurse_shift_counts[nurse_id]['N']
                        
                        total = sum([d_count, e_count, n_count])
                        avg = total / 3 if total > 0 else 0
                        
                        # 해당 근무 유형이 평균보다 적으면 높은 점수
                        current_count = nurse_shift_counts[nurse_id][shift_type]
                        balance_score = 30 if current_count < avg else 0
                        
                        # 남은 근무수가 많은 간호사 선호
                        remaining_score = remaining_shifts_per_nurse[nurse_id] * 2
                        
                        relaxed_candidates.append((balance_score + remaining_score, nurse_id))
                
                # 극단적으로 완화된 제약조건 - 필수 제약 조건 최소화
                elif remaining_shifts_per_nurse[nurse_id] > 0:
                    # 극단적인 경우 N 근무 다음날 OFF만 반드시 지키도록 함
                    can_extreme_assign = True
                    
                    # 나이트 킵 간호사는 N 근무만 배정 가능 (이 조건은 절대 완화하지 않음)
                    nurse = next((n for n in nurse_list if n.id == nurse_id), None)
                    if nurse and nurse.is_night_keeper and shift_type != 'N':
                        can_extreme_assign = False
                    
                    # N 근무 후 무조건 OFF 규칙만 준수 - 절대 완화하지 않음
                    prev_date = day - timedelta(days=1)
                    if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                        can_extreme_assign = False
                    
                    # 2일 전이 N 근무이고 어제가 OFF면 오늘도 무조건 OFF만 가능 - 절대 완화하지 않음
                    prev_2_date = day - timedelta(days=2)
                    if ((nurse_id, prev_2_date) in final_schedule and final_schedule[(nurse_id, prev_2_date)] == 'N') and \
                       ((nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'OFF'):
                        can_extreme_assign = False
                    
                    # E 근무 직후 D 근무 금지만 유지 (이 조건도 절대 완화하지 않음)
                    if shift_type == 'D':
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'E':
                            can_extreme_assign = False
                    
                    # 추가된 제약 조건 (절대 완화하지 않음)
                    # E 근무 앞에 N 근무 금지
                    if shift_type == 'E':
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                            can_extreme_assign = False
                    
                    # D 근무 앞에 N 근무 금지
                    if shift_type == 'D':
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'N':
                            can_extreme_assign = False
                    
                    # E 근무 앞에 E 근무 금지
                    if shift_type == 'E':
                        if (nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'E':
                            can_extreme_assign = False
                    
                    # 다른 제약 조건은 모두 완화 (주간 근무일 제한, 연속 근무일 제한 등)
                    
                    if can_extreme_assign:
                        # 이런 경우 남은 근무수에 우선 배정
                        remaining_score = remaining_shifts_per_nurse[nurse_id] * 3
                        extremely_relaxed_candidates.append((remaining_score, nurse_id))
            
            # 점수가 높은 순으로 정렬
            candidates.sort(reverse=True)
            relaxed_candidates.sort(reverse=True)
            extremely_relaxed_candidates.sort(reverse=True)
            
            # 배정 시도 - 가장 점수 높은 후보부터
            assigned_count = 0
            for _, nurse_id in candidates:
                if assigned_count >= shortage:
                    break
                
                # 근무 배정
                final_schedule[(nurse_id, day)] = shift_type
                remaining_shifts_per_nurse[nurse_id] -= 1
                
                # 간호사의 근무 유형 카운트 업데이트
                nurse_shift_counts[nurse_id][shift_type] += 1
                
                # 간호사의 이전 근무 타입 기록 업데이트
                nurse_previous_shifts[nurse_id].append(shift_type)
                if len(nurse_previous_shifts[nurse_id]) > 3:
                    nurse_previous_shifts[nurse_id].pop(0)
                
                assigned_count += 1
                messages.success(request, f'{day.strftime("%Y-%m-%d")}에 간호사 {nurse_id}에게 {shift_type} 근무를 추가 배정했습니다.')
            
            # 아직 부족하면 완화된 제약 조건으로 추가 배정 시도
            if assigned_count < shortage:
                for _, nurse_id in relaxed_candidates:
                    if assigned_count >= shortage:
                        break
                    
                    # 이미 배정됐다면 건너뛰기
                    if (nurse_id, day) in final_schedule:
                        continue
                    
                    # 근무 배정 (완화된 제약 조건)
                    final_schedule[(nurse_id, day)] = shift_type
                    remaining_shifts_per_nurse[nurse_id] -= 1
                    
                    # 간호사의 근무 유형 카운트 업데이트
                    nurse_shift_counts[nurse_id][shift_type] += 1
                    
                    # 간호사의 이전 근무 타입 기록 업데이트
                    nurse_previous_shifts[nurse_id].append(shift_type)
                    if len(nurse_previous_shifts[nurse_id]) > 3:
                        nurse_previous_shifts[nurse_id].pop(0)
                    
                    assigned_count += 1
                    messages.warning(request, f'{day.strftime("%Y-%m-%d")}에 간호사 {nurse_id}에게 완화된 제약으로 {shift_type} 근무를 배정했습니다.')
            
            # 여전히 부족하면 극단적으로 완화된 제약으로 추가 배정 시도
            if assigned_count < shortage:
                for _, nurse_id in extremely_relaxed_candidates:
                    if assigned_count >= shortage:
                        break
                    
                    # 이미 배정됐다면 건너뛰기
                    if (nurse_id, day) in final_schedule:
                        continue
                    
                    # 근무 배정 (극단적으로 완화된 제약 조건)
                    final_schedule[(nurse_id, day)] = shift_type
                    remaining_shifts_per_nurse[nurse_id] -= 1
                    
                    # 간호사의 근무 유형 카운트 업데이트
                    nurse_shift_counts[nurse_id][shift_type] += 1
                    
                    # 간호사의 이전 근무 타입 기록 업데이트
                    nurse_previous_shifts[nurse_id].append(shift_type)
                    if len(nurse_previous_shifts[nurse_id]) > 3:
                        nurse_previous_shifts[nurse_id].pop(0)
                    
                    assigned_count += 1
                    messages.error(request, f'{day.strftime("%Y-%m-%d")}에 {shift_type} 근무에 심각한 인원 부족으로 간호사 {nurse_id}에게 극단적 제약 완화로 배정했습니다.')
            
            # 모든 시도 후에도 여전히 부족한 경우, 마지막 대안으로 OFF인 간호사를 찾아 재배정
            if assigned_count < shortage:
                # OFF 근무 간호사 찾기
                for (n_id, d), s in final_schedule.items():
                    if assigned_count >= shortage:
                        break
                        
                    if d == day and s == 'OFF' and remaining_shifts_per_nurse[n_id] > 0:
                        nurse = next((n for n in nurse_list if n.id == n_id), None)
                        
                        # 나이트 킵 간호사는 N 근무만 배정 가능 (이 조건은 절대 완화하지 않음)
                        if nurse and nurse.is_night_keeper and shift_type != 'N':
                            continue
                            
                        # N 근무 후 OFF 규칙만 준수 - 절대 완화하지 않음
                        prev_date = day - timedelta(days=1)
                        if (n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'N':
                            continue
                            
                        # 2일 전이 N 근무이고 어제가 OFF면 오늘도 무조건 OFF만 가능 - 절대 완화하지 않음
                        prev_2_date = day - timedelta(days=2)
                        if ((n_id, prev_2_date) in final_schedule and final_schedule[(n_id, prev_2_date)] == 'N') and \
                           ((n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'OFF'):
                            continue
                            
                        # E 근무 다음날에 D 근무 금지 규칙도 유지
                        if shift_type == 'D':
                            if (n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'E':
                                continue
                        
                        # 추가된 제약 조건 (절대 완화하지 않음)
                        # E 근무 앞에 N 근무 금지
                        if shift_type == 'E':
                            if (n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'N':
                                continue
                        
                        # D 근무 앞에 N 근무 금지
                        if shift_type == 'D':
                            if (n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'N':
                                continue
                        
                        # E 근무 앞에 E 근무 금지
                        if shift_type == 'E':
                            if (n_id, prev_date) in final_schedule and final_schedule[(n_id, prev_date)] == 'E':
                                continue
                        
                        # OFF를 취소하고 필요한 근무 유형으로 재배정
                        final_schedule[(n_id, day)] = shift_type
                        
                        # 간호사의 근무 유형 카운트 업데이트
                        nurse_shift_counts[n_id][shift_type] += 1
                        
                        # 간호사의 이전 근무 타입 기록 업데이트
                        nurse_previous_shifts[n_id].append(shift_type)
                        if len(nurse_previous_shifts[n_id]) > 3:
                            nurse_previous_shifts[n_id].pop(0)
                        
                        assigned_count += 1
                        messages.error(request, f'{day.strftime("%Y-%m-%d")}에 {shift_type} 근무에 심각한 인원 부족으로 간호사 {n_id}의 OFF를 취소하고 재배정했습니다.')
        
        # 최종 스케줄 검증 및 필요 인원 보고서 생성
        final_verification_passed = True
        final_report = {}
        
        for day in date_range:
            daily_report = {'D': 0, 'E': 0, 'N': 0, 'OFF': 0}
            
            for (n_id, d), s in final_schedule.items():
                if d == day:
                    if s in daily_report:
                        daily_report[s] += 1
            
            # 필요 인원 검증
            for shift_type in ['D', 'E', 'N']:
                required = daily_shift_requirements[day][shift_type]
                actual = daily_report[shift_type]
                
                if actual < required:
                    final_verification_passed = False
                    messages.error(request, f'최종 검증: {day.strftime("%Y-%m-%d")}에 {shift_type} 근무가 {required-actual}명 부족합니다.')
            
            final_report[day] = daily_report
        
        # 최종 균형 상태 확인 및 정보 제공
        nurse_final_counts = {nurse.id: {'D': 0, 'E': 0, 'N': 0} for nurse in nurse_list}
        for (nurse_id, date), shift in final_schedule.items():
            if shift in ['D', 'E', 'N']:
                nurse_final_counts[nurse_id][shift] += 1
        
        # 균형 정보 메시지 추가
        balance_info = []
        for nurse in nurse_list:
            counts = nurse_final_counts[nurse.id]
            total = sum(counts.values())
            if total > 0:
                avg = total / 3
                max_diff = max([abs(counts['D'] - avg), abs(counts['E'] - avg), abs(counts['N'] - avg)])
                balance_info.append(f"{nurse.name}: D={counts['D']}, E={counts['E']}, N={counts['N']}, 편차={max_diff:.1f}")
        
        messages.info(request, '각 간호사별 근무 유형 분포: ' + ' | '.join(balance_info))
        
        # 최종 스케줄 검증 - 중요 제약 조건 확인
        validation_errors = []
        
        # 각 간호사 스케줄 검증
        for nurse in nurse_list:
            nurse_id = nurse.id
            is_night_keeper = nurse.is_night_keeper
            
            # 나이트킵 간호사 확인
            if is_night_keeper:
                for day in date_range:
                    if (nurse_id, day) in final_schedule:
                        shift = final_schedule[(nurse_id, day)]
                        if shift not in ['N', 'OFF']:
                            error_msg = f"심각한 오류: 나이트킵 간호사 {nurse.name}에게 {day.strftime('%Y-%m-%d')}에 {shift} 근무가 배정됨"
                            validation_errors.append(error_msg)
                            # 강제로 수정
                            final_schedule[(nurse_id, day)] = 'OFF'
            
            # 연속 N 근무 후 2일 OFF 검증
            for i, day in enumerate(date_range):
                if (nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] == 'N':
                    # 연속된 N 근무 확인
                    is_last_n = True
                    next_day = day + timedelta(days=1)
                    
                    # 다음날이 N이면 마지막 N이 아님
                    if i < len(date_range) - 1 and (nurse_id, next_day) in final_schedule and final_schedule[(nurse_id, next_day)] == 'N':
                        is_last_n = False
                    
                    # 마지막 N이면 다음 2일 OFF 확인
                    if is_last_n:
                        for j in range(1, 3):  # 다음 2일 확인
                            if i + j < len(date_range):
                                check_day = day + timedelta(days=j)
                                if (nurse_id, check_day) in final_schedule:
                                    if final_schedule[(nurse_id, check_day)] != 'OFF':
                                        error_msg = f"심각한 오류: {nurse.name}의 {day.strftime('%Y-%m-%d')} N 근무 후 {check_day.strftime('%Y-%m-%d')}에 OFF가 아닌 {final_schedule[(nurse_id, check_day)]} 근무가 배정됨 (사유: N 근무 후 신체회복을 위해 반드시 2일의 OFF가 필요함)"
                                        validation_errors.append(error_msg)
                                        # 강제로 수정
                                        final_schedule[(nurse_id, check_day)] = 'OFF'
        
        # 검증 오류 메시지 표시
        if validation_errors:
            for error in validation_errors:
                messages.error(request, error)
            messages.warning(request, "일부 제약 조건 위반이 감지되어 자동으로 수정되었습니다. (간호사 안전과 효율적 근무 환경을 위한 필수 제약조건 준수)")
        
        # 단일 N 근무 및 OFF-N-OFF 패턴 검증 및 수정
        single_n_validation_errors = []
        
        for nurse in nurse_list:
            nurse_id = nurse.id
            
            # 모든 날짜에 대해 검사
            for i, day in enumerate(date_range):
                if i == 0 or i >= len(date_range) - 1:
                    continue  # 첫날과 마지막 날은 패턴 검사에서 제외
                
                # 단일 N 근무 검사 (N 근무 앞뒤로 N이 아닌 경우)
                if (nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] == 'N':
                    prev_date = day - timedelta(days=1)
                    next_date = day + timedelta(days=1)
                    
                    prev_shift = final_schedule.get((nurse_id, prev_date), None)
                    next_shift = final_schedule.get((nurse_id, next_date), None)
                    
                    # 단일 N 근무 감지 (앞뒤가 N이 아님)
                    if prev_shift != 'N' and next_shift != 'N':
                        error_msg = f"단일 N 근무 감지: {nurse.name}의 {day.strftime('%Y-%m-%d')}에 단일 N 근무가 배정됨"
                        single_n_validation_errors.append(error_msg)
                        
                        # N 근무를 OFF로 변경
                        final_schedule[(nurse_id, day)] = 'OFF'
                        
                        # 다른 간호사에게 N 배정 시도
                        for other_nurse in nurse_list:
                            if other_nurse.id == nurse_id:
                                continue
                            
                            # 이미 해당 날짜에 근무 중이면 제외
                            if (other_nurse.id, day) in final_schedule and final_schedule[(other_nurse.id, day)] != 'OFF':
                                continue
                            
                            # 어제가 N이면 오늘은 OFF만 가능
                            prev_1_date = day - timedelta(days=1)
                            if (other_nurse.id, prev_1_date) in final_schedule and final_schedule[(other_nurse.id, prev_1_date)] == 'N':
                                continue
                            
                            # 2일 전이 N이고 어제가 OFF면 오늘도 OFF만 가능
                            prev_2_date = day - timedelta(days=2)
                            if ((other_nurse.id, prev_2_date) in final_schedule and final_schedule[(other_nurse.id, prev_2_date)] == 'N') and \
                               ((other_nurse.id, prev_1_date) in final_schedule and final_schedule[(other_nurse.id, prev_1_date)] == 'OFF'):
                                continue
                            
                            # N 배정 후 다음 2일이 이미 다른 근무로 배정되어 있으면 N 배정 불가
                            next_1_date = day + timedelta(days=1)
                            next_2_date = day + timedelta(days=2)
                            
                            if next_1_date <= end_date and (other_nurse.id, next_1_date) in final_schedule and final_schedule[(other_nurse.id, next_1_date)] != 'OFF' and final_schedule[(other_nurse.id, next_1_date)] != 'N':
                                continue
                            
                            if next_2_date <= end_date and (other_nurse.id, next_2_date) in final_schedule and final_schedule[(other_nurse.id, next_2_date)] != 'OFF':
                                continue
                            
                            # N 근무 배정 후 다음날도 N 근무로 지정
                            final_schedule[(other_nurse.id, day)] = 'N'
                            
                            # 다음날이 아직 배정되지 않았거나 OFF면 N으로 배정
                            if next_1_date <= end_date and ((other_nurse.id, next_1_date) not in final_schedule or final_schedule[(other_nurse.id, next_1_date)] == 'OFF'):
                                final_schedule[(other_nurse.id, next_1_date)] = 'N'
                                
                                # N 근무 후 2일 OFF 예약
                                for j in range(1, 3):
                                    off_date = next_1_date + timedelta(days=j)
                                    if off_date <= end_date:
                                        final_schedule[(other_nurse.id, off_date)] = 'OFF'
                            
                            messages.success(request, f"단일 N 근무 수정: {day.strftime('%Y-%m-%d')}에 {nurse.name} 대신 {other_nurse.name}에게 N 근무 배정 (사유: 생체리듬 보호 및 효율적 인력 운영을 위해 연속 N 패턴 적용)")
                            break
                
                # OFF-N-OFF 패턴 검사
                if i > 0 and i < len(date_range) - 1:
                    prev_date = day - timedelta(days=1)
                    next_date = day + timedelta(days=1)
                    
                    if ((nurse_id, prev_date) in final_schedule and final_schedule[(nurse_id, prev_date)] == 'OFF') and \
                       ((nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] == 'N') and \
                       ((nurse_id, next_date) in final_schedule and final_schedule[(nurse_id, next_date)] == 'OFF'):
                        
                        error_msg = f"OFF-N-OFF 패턴 감지: {nurse.name}의 {day.strftime('%Y-%m-%d')}에 단일 N 근무가 OFF 사이에 배정됨 (사유: 생체리듬 교란 방지 및 효율적 인력 활용을 위해 단일 N 패턴 제거)"
                        single_n_validation_errors.append(error_msg)
                        
                        # N 근무를 OFF로 변경
                        final_schedule[(nurse_id, day)] = 'OFF'
        
        # 단일 N 근무 검증 오류 메시지 표시
        if single_n_validation_errors:
            for error in single_n_validation_errors:
                messages.error(request, error)
            messages.warning(request, "단일 N 근무 및 OFF-N-OFF 패턴이 감지되어 자동으로 수정되었습니다. (생체리듬 보호 및 효율적 인력 운영을 위한 연속 N 패턴 적용 필요)")
        
        # 일일 근무 인원수 검증 및 보완 (필요 인원수를 반드시 충족하도록)
        messages.info(request, "일일 근무 인원수 최종 검증 및 보완 시작...")
        daily_assignments = {}
        
        # 일별 근무 배정 현황 파악
        for (nurse_id, day), shift in final_schedule.items():
            if shift in ['D', 'E', 'N']:
                if (day, shift) not in daily_assignments:
                    daily_assignments[(day, shift)] = []
                daily_assignments[(day, shift)].append(nurse_id)
        
        # 부족한 인원 파악
        additional_assignments_needed = []
        total_shortage = 0
        for day in date_range:
            for shift_type in ['D', 'E', 'N']:
                required = daily_shift_requirements[day][shift_type]
                current = len(daily_assignments.get((day, shift_type), []))
                
                if current < required:
                    shortage = required - current
                    additional_assignments_needed.append((day, shift_type, shortage))
                    total_shortage += shortage
        
        # 부족한 근무 배정 해결
        if additional_assignments_needed:
            messages.warning(request, f"{len(additional_assignments_needed)}개의 근무 인원 부족 문제 발견, 총 {total_shortage}명 부족. 추가 배정 시작.")
            
            # 부족한 인원이 많은 순서로 정렬
            additional_assignments_needed.sort(key=lambda x: x[2], reverse=True)
            
            for day, shift_type, shortage in additional_assignments_needed:
                day_idx = (day - start_date).days
                messages.warning(request, f"{day.strftime('%Y-%m-%d')}의 {shift_type} 근무가 {shortage}명 부족합니다. 추가 배정 시작.")
                
                # 근무별 적합한 간호사 후보 찾기
                candidates = []
                
                for nurse_id, nurse in nurses_by_id.items():
                    # 이미 해당 날짜에 배정된 간호사는 건너뜀
                    if (nurse_id, day) in final_schedule and final_schedule[(nurse_id, day)] != 'OFF':
                        continue
                    
                    # 나이트 킵 간호사는 N 근무에만 배정 가능
                    if nurse.is_night_keeper and shift_type != 'N':
                        continue
                    
                    # 필수 제약 조건 확인 (Hard constraints)
                    can_assign = True
                    
                    # 어제가 N이면 오늘은 OFF만 가능 (절대 완화 불가)
                    prev_1_date = day - timedelta(days=1)
                    if (nurse_id, prev_1_date) in final_schedule and final_schedule[(nurse_id, prev_1_date)] == 'N':
                        can_assign = False
                        continue
                    
                    # 2일 전이 N이고 어제가 OFF면 오늘도 OFF만 가능 (절대 완화 불가)
                    prev_2_date = day - timedelta(days=2)
                    if ((nurse_id, prev_2_date) in final_schedule and final_schedule[(nurse_id, prev_2_date)] == 'N') and \
                       ((nurse_id, prev_1_date) in final_schedule and final_schedule[(nurse_id, prev_1_date)] == 'OFF'):
                        can_assign = False
                        continue
                    
                    # E 근무 다음 날에 D 근무 금지 (필수 규칙)
                    if shift_type == 'D' and (nurse_id, prev_1_date) in final_schedule and final_schedule[(nurse_id, prev_1_date)] == 'E':
                        can_assign = False
                        continue
                    
                    # 일주일에 6회 이상 근무 금지 검증 (필수 규칙)
                    # 현재 날짜가 속한 주의 시작일과 종료일 계산
                    week_start = day - timedelta(days=day.weekday())
                    week_end = week_start + timedelta(days=6)
                    
                    # 주간 근무 수 계산
                    week_work_count = 0
                    for d in [week_start + timedelta(days=i) for i in range(7)]:
                        if d in date_range:  # 스케줄 범위 내의 날짜만 확인
                            if (nurse_id, d) in final_schedule and final_schedule[(nurse_id, d)] in ['D', 'E', 'N']:
                                week_work_count += 1
                    
                    # 현재 날짜에 근무 배정 시 주간 근무 수가 6을 초과하면 배정 금지
                    if (nurse_id, day) not in final_schedule or final_schedule[(nurse_id, day)] == 'OFF':  # 새 근무 배정일 경우
                        if week_work_count >= 6:
                            can_assign = False
                            continue
                    
                    # 연속 6일 이상 근무 금지 (필수 규칙)
                    consecutive_work_days = 0
                    for i in range(6):  # 오늘 기준 이전 5일 확인
                        check_date = day - timedelta(days=i+1)
                        if check_date >= start_date:  # 스케줄 범위 내의 날짜만 확인
                            if (nurse_id, check_date) in final_schedule and final_schedule[(nurse_id, check_date)] in ['D', 'E', 'N']:
                                consecutive_work_days += 1
                            else:
                                break  # 연속이 끊기면 중단
                    
                    # 이미 5일 연속 근무했다면 오늘은 OFF여야 함
                    if consecutive_work_days >= 5:
                        can_assign = False
                        continue
                    
                    # 배정 가능한 경우 점수 계산
                    if can_assign:
                        # 근무 유형 분포 점수
                        d_count = sum(1 for (n_id, d), s in final_schedule.items() if n_id == nurse_id and s == 'D')
                        e_count = sum(1 for (n_id, d), s in final_schedule.items() if n_id == nurse_id and s == 'E')
                        n_count = sum(1 for (n_id, d), s in final_schedule.items() if n_id == nurse_id and s == 'N')
                        
                        current_count = {'D': d_count, 'E': e_count, 'N': n_count}
                        total_shifts = d_count + e_count + n_count
                        
                        if total_shifts > 0:
                            # 현재 근무 유형의 비율이 낮은 간호사 선호
                            shift_ratio = current_count[shift_type] / total_shifts
                            balance_score = 100 * (0.33 - shift_ratio) if shift_ratio < 0.33 else 0
                        else:
                            balance_score = 50
                        
                        # 총 근무수가 적은 간호사 우선
                        workload_score = 100 - (total_shifts * 5)  # 근무수가 적을수록 높은 점수
                        
                        # 최종 점수
                        total_score = balance_score + workload_score
                        candidates.append((total_score, nurse_id))
                
                # 점수 높은 순으로 정렬
                candidates.sort(reverse=True)
                
                # 필요한 만큼 추가 배정
                assigned_count = 0
                for _, nurse_id in candidates:
                    if assigned_count >= shortage:
                        break
                    
                    nurse = nurses_by_id[nurse_id]
                    
                    # 최종 배정 전 제약 조건 확인
                    can_final_assign = True
                    
                    # N 근무 배정 시 다음 2일 OFF 예약
                    if shift_type == 'N':
                        next_1_date = day + timedelta(days=1)
                        next_2_date = day + timedelta(days=2)
                        
                        # 다음 날짜들에 OFF 배정 가능한지 확인
                        if next_1_date <= end_date:
                            # 다음 날이 이미 다른 근무로 배정되어 있고 N이 아니면 배정 불가
                            if (nurse_id, next_1_date) in final_schedule and final_schedule[(nurse_id, next_1_date)] != 'N' and final_schedule[(nurse_id, next_1_date)] != 'OFF':
                                can_final_assign = False
                                continue
                                
                            # 다음 날이 N이 아니면 반드시 OFF 배정
                            if (nurse_id, next_1_date) not in final_schedule or final_schedule[(nurse_id, next_1_date)] != 'N':
                                final_schedule[(nurse_id, next_1_date)] = 'OFF'
                                
                        if next_2_date <= end_date:
                            # 다음 날이 N이 아니면 다다음 날도 OFF 배정
                            if next_1_date <= end_date and ((nurse_id, next_1_date) not in final_schedule or final_schedule[(nurse_id, next_1_date)] != 'N'):
                                # 다다음 날이 이미 다른 근무로 배정되어 있으면 배정 불가
                                if (nurse_id, next_2_date) in final_schedule and final_schedule[(nurse_id, next_2_date)] != 'OFF':
                                    can_final_assign = False
                                    # 앞서 배정한 OFF 취소
                                    if next_1_date <= end_date and (nurse_id, next_1_date) in final_schedule and final_schedule[(nurse_id, next_1_date)] == 'OFF':
                                        del final_schedule[(nurse_id, next_1_date)]
                                    continue
                                    
                                # 다다음 날짜에 OFF 배정
                                final_schedule[(nurse_id, next_2_date)] = 'OFF'
                    
                    # 최종 배정
                    if can_final_assign:
                        # 이전 배정이 있으면 OFF로 변경
                        if (nurse_id, day) in final_schedule:
                            final_schedule[(nurse_id, day)] = shift_type
                        else:
                            final_schedule[(nurse_id, day)] = shift_type
                        
                        # 배정 성공 카운트
                        assigned_count += 1
                        messages.success(request, f"{day.strftime('%Y-%m-%d')}에 {nurse.name}을(를) {shift_type} 근무에 추가 배정했습니다.")
                
                # 여전히 부족하면 경고
                if assigned_count < shortage:
                    remaining = shortage - assigned_count
                    messages.error(request, f"{day.strftime('%Y-%m-%d')}의 {shift_type} 근무가 여전히 {remaining}명 부족합니다. 제약 조건으로 인해 더 이상 배정할 수 없습니다.")
        
        # 11. 데이터베이스에 스케줄 저장
        # 최종 근무 인원 현황 파악 및 보고
        updated_daily_assignments = {}
        for (nurse_id, day), shift in final_schedule.items():
            if shift in ['D', 'E', 'N']:
                if (day, shift) not in updated_daily_assignments:
                    updated_daily_assignments[(day, shift)] = []
                updated_daily_assignments[(day, shift)].append(nurse_id)
        
        staffing_report = []
        for day in date_range:
            for shift_type in ['D', 'E', 'N']:
                required = shift_requirements.get(shift_type, 4)
                current = len(updated_daily_assignments.get((day, shift_type), []))
                status = "충족" if current >= required else f"부족 ({current}/{required})"
                staffing_report.append(f"{day.strftime('%Y-%m-%d')}의 {shift_type} 근무: {status}")
        
        messages.info(request, "최종 근무 인원 현황: " + " | ".join(staffing_report[:10]) + (f" 외 {len(staffing_report)-10}건" if len(staffing_report) > 10 else ""))
        
        # 스케줄 저장 - 중복 방지 로직 추가
        saved_count = 0
        skipped_count = 0
        for (nurse_id, date), shift in final_schedule.items():
            try:
                nurse = Nurse.objects.get(id=nurse_id)
                # get_or_create 사용하여 중복 방지
                schedule, created = Schedule.objects.get_or_create(
                    nurse=nurse,
                    date=date,
                    defaults={'shift': shift}
                )
                
                # 기존 스케줄이 있지만 근무 유형이 다른 경우 업데이트
                if not created and schedule.shift != shift:
                    schedule.shift = shift
                    schedule.save()
                
                saved_count += 1
            except Exception as e:
                print(f"스케줄 저장 중 오류 발생: {str(e)}, 간호사 ID: {nurse_id}, 날짜: {date}, 근무: {shift}")
                skipped_count += 1
        
        messages.success(request, f'성공: 근무표가 생성되었습니다. {saved_count}개의 스케줄이 저장되었습니다. {skipped_count}개는 건너뛰었습니다.')
        
    except Exception as e:
        # 오류 발생 시 로그 출력
        import traceback
        traceback.print_exc()
        messages.error(request, f'오류: 근무표 생성 중 오류가 발생했습니다. {str(e)}')
        
def regenerate_schedule(request):
    """기존 근무표를 동일한 조건으로 재생성하는 함수"""
    try:
        # 1. 기존 근무표의 날짜 범위와 나이트 킵 간호사 정보 확인
        all_schedules = Schedule.objects.all().order_by('date')
        if not all_schedules.exists():
            messages.error(request, '재생성할 근무표가 없습니다.')
            return redirect('view_schedule')
            
        min_date = all_schedules.order_by('date').first().date
        max_date = all_schedules.order_by('-date').first().date
        nurses = Nurse.objects.all()
        
        # 2. 현재 근무 요구사항 정보 가져오기
        staffing_requirements = StaffingRequirement.objects.all()
        if not staffing_requirements.exists():
            messages.error(request, '근무별 필요 인원 설정이 되어 있지 않습니다.')
            return redirect('view_schedule')
        
        # 3. 날짜 범위 계산
        date_range = []
        current_date = min_date
        while current_date <= max_date:
            date_range.append(current_date)
            current_date += timedelta(days=1)
        
        total_days = len(date_range)
        
        # 4. 근무별 필요 인원 설정 계산
        shift_requirements = {}
        for req in staffing_requirements:
            shift_requirements[req.shift] = req.required_staff
        
        # 기본값 설정
        if 'D' not in shift_requirements: shift_requirements['D'] = 1
        if 'E' not in shift_requirements: shift_requirements['E'] = 1
        if 'N' not in shift_requirements: shift_requirements['N'] = 1
        
        # 5. 간호사별 할당 근무수 계산
        nurse_shifts = {}
        for nurse in nurses:
            d_count = Schedule.objects.filter(nurse=nurse, shift='D').count()
            e_count = Schedule.objects.filter(nurse=nurse, shift='E').count()
            n_count = Schedule.objects.filter(nurse=nurse, shift='N').count()
            nurse_shifts[nurse.id] = d_count + e_count + n_count
        
        # 6. 기존 스케줄 삭제
        existing_schedules = Schedule.objects.filter(date__range=[min_date, max_date])
        if existing_schedules.exists():
            delete_count = existing_schedules.count()
            existing_schedules.delete()
            messages.info(request, f'기존 스케줄 {delete_count}개가 삭제되었습니다. 새 스케줄을 생성합니다.')
        
        # 7. 스케줄 생성 로직 호출 - 같은 간호사 구성, 같은 날짜 범위, 같은 필요 인원으로 재생성
        # 근무 패턴은 랜덤성으로 인해 달라질 수 있음
        messages.info(request, f'근무표를 재생성합니다. ({min_date.strftime("%Y-%m-%d")} ~ {max_date.strftime("%Y-%m-%d")})')
        create_schedule_with_pattern(request, min_date, max_date, nurses, nurse_shifts, shift_requirements)
        
        messages.success(request, '근무표가 성공적으로 재생성되었습니다.')
        return redirect('view_schedule')
    except Exception as e:
        messages.error(request, f'근무표 재생성 중 오류가 발생했습니다: {str(e)}')
        return redirect('view_schedule')

def view_schedule(request):
    """스케줄 조회 뷰"""
    schedules = Schedule.objects.all().order_by('date')
    
    if not schedules.exists():
        messages.warning(request, '생성된 스케줄이 없습니다.')
        return render(request, 'scheduler/view_schedule.html', {'has_schedules': False})
    
    # 날짜 범위 계산
    min_date = schedules.order_by('date').first().date
    max_date = schedules.order_by('-date').first().date
    
    # 모든 간호사 정보 가져오기
    nurses = Nurse.objects.all().order_by('name')
    
    # 날짜 범위
    date_range = []
    current_date = min_date
    while current_date <= max_date:
        date_range.append(current_date)
        current_date += timedelta(days=1)
    
    # 각 간호사별 스케줄 정보
    schedule_data = {}
    for nurse in nurses:
        schedule_data[nurse.id] = {}
        
    for schedule in schedules:
        # 날짜를 문자열로 변환하지 않고 직접 사용
        schedule_data[schedule.nurse.id][schedule.date] = schedule.shift
    
    # 변경 이력 가져오기
    shift_change_history = {}
    for nurse in nurses:
        shift_change_history[nurse.id] = {}
        for date in date_range:
            # 날짜를 문자열로 변환하지 않고 직접 사용
            shift_change_history[nurse.id][date] = []
    
    # 근무 변경 이력 조회
    from .models import ShiftChangeHistory
    change_histories = ShiftChangeHistory.objects.all().order_by('nurse', 'date', 'change_number')
    
    for history in change_histories:
        nurse_id = history.nurse.id
        date = history.date
        
        if nurse_id in shift_change_history and date in shift_change_history[nurse_id]:
            shift_change_history[nurse_id][date].append({
                'previous_shift': history.previous_shift,
                'new_shift': history.new_shift,
                'change_time': history.change_time,
                'change_number': history.change_number
            })
    
    # 요일 정보 추가
    day_names = ['월', '화', '수', '목', '금', '토', '일']
    date_headers = [(d, day_names[d.weekday()]) for d in date_range]
    
    # 근무별 필요 인원 설정
    staffing_requirements = {}
    for req in StaffingRequirement.objects.all():
        staffing_requirements[req.shift] = req.required_staff
    
    # 기본값 설정
    if 'D' not in staffing_requirements: staffing_requirements['D'] = 4
    if 'E' not in staffing_requirements: staffing_requirements['E'] = 4
    if 'N' not in staffing_requirements: staffing_requirements['N'] = 4
    
    context = {
        'nurses': nurses,
        'dates': date_range,
        'date_headers': date_headers,
        'schedule_data': schedule_data,
        'shift_change_history': shift_change_history,  # 변경 이력 추가
        'staffing_requirements': staffing_requirements,
        'min_date': min_date,
        'max_date': max_date,
        'has_schedules': True
    }
    
    return render(request, 'scheduler/view_schedule.html', context)

def analyze_schedule_view(request):
    """스케줄 분석 뷰"""
    from .utils import analyze_schedule
    
    schedules = Schedule.objects.all().order_by('date')
    
    if not schedules.exists():
        messages.warning(request, '분석할 스케줄이 없습니다.')
        return redirect('generate_schedule')
    
    # 날짜 범위 계산
    min_date = schedules.order_by('date').first().date
    max_date = schedules.order_by('-date').first().date
    
    # 날짜 범위
    date_range = []
    current_date = min_date
    while current_date <= max_date:
        date_range.append(current_date)
        current_date += timedelta(days=1)
    
    # 각 간호사별 스케줄 정보
    schedule_data = {}
    for schedule in schedules:
        schedule_data[(schedule.nurse.id, schedule.date)] = schedule.shift
    
    # 근무별 필요 인원 설정
    staffing_requirements = {}
    for req in StaffingRequirement.objects.all():
        staffing_requirements[req.shift] = req.required_staff
    
    # 기본값 설정
    if 'D' not in staffing_requirements: staffing_requirements['D'] = 4
    if 'E' not in staffing_requirements: staffing_requirements['E'] = 4
    if 'N' not in staffing_requirements: staffing_requirements['N'] = 4
    
    # 스케줄 분석
    analysis_result = analyze_schedule(schedule_data, date_range, staffing_requirements)
    
    # 문제점 분류
    understaffed_problems = [p for p in analysis_result['problems'] if p['type'] == 'understaffed']
    consecutive_work_problems = [p for p in analysis_result['problems'] if p['type'] == 'consecutive_work']
    weekly_work_problems = [p for p in analysis_result['problems'] if p['type'] == 'weekly_work']
    e_d_pattern_problems = [p for p in analysis_result['problems'] if p['type'] == 'e_d_pattern']
    single_n_problems = [p for p in analysis_result['problems'] if p['type'] == 'single_n']
    
    # 각 일자별 근무 인원수 통계
    daily_stats = analysis_result['daily_stats']
    
    # 간호사 목록
    nurses = Nurse.objects.all().order_by('name')
    
    context = {
        'analysis_result': analysis_result,
        'understaffed_problems': understaffed_problems,
        'consecutive_work_problems': consecutive_work_problems,
        'weekly_work_problems': weekly_work_problems,
        'e_d_pattern_problems': e_d_pattern_problems,
        'single_n_problems': single_n_problems,
        'daily_stats': daily_stats,
        'date_range': date_range,
        'nurses': nurses,
        'schedule_data': schedule_data,
    }
    
    return render(request, 'scheduler/analyze_schedule.html', context)

def calculate_pattern_score(nurse_id, date, shift, last_5_shifts=None):
    """
    간호사의 이전 근무 패턴에 따른 점수를 계산합니다.
    """
    score = 0
    
    # nurse_id가 정수인 경우 nurse 객체로 변환
    if isinstance(nurse_id, int):
        try:
            nurse = Nurse.objects.get(id=nurse_id)
        except Nurse.DoesNotExist:
            # 간호사를 찾을 수 없는 경우 기본 점수 0 반환
            return 0
    else:
        nurse = nurse_id  # 이미 nurse 객체인 경우
    
    # 이전 5일간의 근무 기록이 없으면 기본값으로 0 반환
    if not last_5_shifts:
        return 0
        
    # ... existing code ...

def delete_schedule(request):
    """모든 근무표를 삭제하는 기능"""
    if request.method == 'POST':
        try:
            # 모든 스케줄 삭제
            Schedule.objects.all().delete()
            
            # 변경 이력도 삭제 (선택 사항)
            try:
                from .models import ShiftChangeHistory
                ShiftChangeHistory.objects.all().delete()
            except:
                pass  # 변경 이력 삭제 실패해도 계속 진행
                
            messages.success(request, '모든 근무표가 성공적으로 삭제되었습니다.')
        except Exception as e:
            messages.error(request, f'근무표 삭제 중 오류가 발생했습니다: {str(e)}')
    
    return redirect('view_schedule')
