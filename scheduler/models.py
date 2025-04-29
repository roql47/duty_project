from django.db import models

# Create your models here.

class Nurse(models.Model):
    """간호사 모델"""
    name = models.CharField(max_length=100, verbose_name="이름")
    employee_id = models.CharField(max_length=20, unique=True, verbose_name="사번")
    is_night_keeper = models.BooleanField(default=False, verbose_name="나이트 킵")
    skill_level = models.IntegerField(default=1, choices=[
        (1, '초급 1'),
        (2, '초급 2'),
        (3, '중급 1'),
        (4, '중급 2'),
        (5, '고급 1'),
        (6, '고급 2'),
    ], verbose_name="숙련도")
    
    class Meta:
        verbose_name = "간호사"
        verbose_name_plural = "간호사들"
        
    def __str__(self):
        return f"{self.name} ({self.employee_id}){' - 나이트 킵' if self.is_night_keeper else ''}"

class Schedule(models.Model):
    SHIFT_CHOICES = [
        ('D', '데이'),
        ('E', '이브닝'),
        ('N', '나이트'),
        ('OFF', '휴무'),
    ]
    
    nurse = models.ForeignKey(Nurse, on_delete=models.CASCADE)
    date = models.DateField()
    shift = models.CharField(max_length=3, choices=SHIFT_CHOICES)
    
    class Meta:
        unique_together = ['nurse', 'date']
    
    def __str__(self):
        return f"{self.nurse.name} - {self.date} - {self.get_shift_display()}"

class ShiftChangeHistory(models.Model):
    """근무 변경 내역을 저장하는 모델"""
    SHIFT_CHOICES = [
        ('D', '데이'),
        ('E', '이브닝'),
        ('N', '나이트'),
        ('OFF', '휴무'),
    ]
    
    nurse = models.ForeignKey(Nurse, on_delete=models.CASCADE)
    date = models.DateField()
    previous_shift = models.CharField(max_length=3, choices=SHIFT_CHOICES)
    new_shift = models.CharField(max_length=3, choices=SHIFT_CHOICES)
    change_time = models.DateTimeField(auto_now_add=True)
    change_number = models.PositiveIntegerField(default=1, help_text="해당 날짜/간호사에 대한 변경 순서 번호")
    
    class Meta:
        ordering = ['-change_time']
    
    def __str__(self):
        return f"{self.nurse.name} - {self.date} - {self.previous_shift} → {self.new_shift}"

class StaffingRequirement(models.Model):
    """각 근무 코드별 필요 인원 수 모델"""
    SHIFT_CHOICES = [
        ('D', '데이'),
        ('E', '이브닝'),
        ('N', '나이트'),
    ]
    
    shift = models.CharField(max_length=3, choices=SHIFT_CHOICES, unique=True)
    required_staff = models.PositiveIntegerField(default=1)
    
    def __str__(self):
        return f"{self.get_shift_display()} - {self.required_staff}명"
