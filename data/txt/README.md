# README

## 数据格式


标签顺序：
运动,休闲,OL/通勤,日系,韩版,欧美,英伦,少女,名媛/淑女,简约,自然,街头/朋克,民族

训练数据(样例：train.txt)
前两个字段分别是图像ID，图像url，后面字段分别按顺序对应上面标签是否出现，1表示出现，0表示没有

测试数据(样例：test.txt)
两个字段分别是图像ID，图像url。

提交数据(样例：sample-submit.txt)
提交数据为测试数据的识别结果
第一个字段是图像ID，后面字段分别按顺序对应提交结果中上面标签是否出现，1表示出现，0表示没有




## k-fold.txt

记录了数据集的划分情况：将训练数据集划分为了5个部分，做为5-fold cross validation

格式：

+ fold,filename

例如:

+ 1,picture1.jpg
+ 2,picture2.jpg


## 数据分布
如果全部输出为0，那么Accuracy可以达到约80%

对象平均标签数量:	 2.6472998816136966  2.6497905663813515  2.652308532920499  2.6497905663813515  2.651429612092515

================================================================

对象平均标签数量:	 2.6472998816136966  2.6497905663813515  2.652308532920499  2.6497905663813515  2.651429612092515

================================================================

单个标签的平均出现次数:

----------------------------------------------------------------

    运动:	 0.0017302613605318278	0.0024585685667455836	0.0017302613605318278	0.0014569295210344199	0.0020943361864869787

----------------------------------------------------------------

    休闲:	 0.08150441671978872	0.07858313604079403	0.07740642928695018	0.07758149699508286	0.07576033509378984

----------------------------------------------------------------

    OL/通勤:	 0.9065658865312813	0.9074849754143144	0.9093889445405701	0.9102167182662538	0.909670369695866

----------------------------------------------------------------

    日系:	 0.005099717694199071	0.004461846658167911	0.004006921045442128	0.005645601894008377	0.0046439628482972135

----------------------------------------------------------------

    韩版:	 0.3444130771332301	0.3413767983973775	0.3466897368181404	0.3367328355490803	0.3356401384083045

----------------------------------------------------------------

    欧美:	 0.1595483107185138	0.16235658350027318	0.15900191239413533	0.1609907120743034	0.16690948825350574

----------------------------------------------------------------

    英伦:	 0.0030962571714780074	0.0033691495173920963	0.004006921045442128	0.0038244399927153524	0.00364232380258605

----------------------------------------------------------------

    少女:	 0.023495127948274293	0.025132034237843746	0.025680721245788178	0.02485885995264979	0.024312511382261884

----------------------------------------------------------------

    名媛/淑女:	 0.8978235133412258	0.8995629211436896	0.9010108369001002	0.8989255144782371	0.9001092697140776

----------------------------------------------------------------

    简约:	 0.12740187596758037	0.12484064833363687	0.1284036062289409	0.13230741212893826	0.13303587688945548

----------------------------------------------------------------

    自然:	 0.06711592751115564	0.07002367510471681	0.06547673253802022	0.06856674558368239	0.06683664177745402

----------------------------------------------------------------

    街头/朋克:	 0.023495127948274293	0.022946639956292113	0.022220198524724524	0.023128756146421417	0.02194500091058095

----------------------------------------------------------------

    民族:	 0.006010381568163191	0.007193589510107449	0.007285310991712959	0.005554543798943726	0.006829357129848844

----------------------------------------------------------------

================================================================

不同的标签组合数量:	 107  120  114  118  112

================================================================

不同的标签组合密度:	 0.009744103451416082  0.010926971407758149  0.010381568163190967  0.010744855217628847  0.01019850664724094